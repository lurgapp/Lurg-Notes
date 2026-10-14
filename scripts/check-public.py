#!/usr/bin/env python3
"""Read-only checks for the source tree prepared for public distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

MANIFEST_NAME = "SOURCE_MANIFEST.json"
FORBIDDEN_DIRS = {
    ".aws", ".cache", ".gradle", ".idea", ".next", ".pytest_cache", ".ruff_cache",
    ".venv", "__pycache__", "artifacts", "backup", "backups", "build", "keys",
    "node_modules", "secrets", "venv",
}
FORBIDDEN_NAMES = {
    ".netrc", "audit-credentials.json", "credentials.json",
    "google-services.json", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa",
    "local.properties", "signing.properties",
}
FORBIDDEN_SUFFIXES = {
    ".aab", ".apk", ".bak", ".backup", ".class", ".db", ".db3",
    ".dump", ".jks", ".key", ".keystore", ".log", ".p12", ".pfx",
    ".pem", ".pyc", ".pyo", ".sqlite", ".sqlite3", ".sqlitedb",
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_SOURCE_ARTIFACTS = {"artifacts/implementation-2026-10-03/nearby-lcp-events.py"}
SECRET_PATTERNS = (
    (
        "private key material",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
            r"(?:\s|\\r|\\n)+[A-Za-z0-9+/=]{40,}"
        ),
    ),
    ("Firebase/Google API key", re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])")),
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("OpenAI API key", re.compile(r"(?<![A-Za-z0-9_-])sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")),
    ("Anthropic API key", re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Stripe live secret key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}\b")),
    ("Telegram bot token", re.compile(r"(?<![A-Za-z0-9_-])[0-9]{7,12}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    (
        "literal JWT",
        re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
    ),
)


def example_name(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".example", ".sample", ".template")) or any(
        marker in lower for marker in (".example.", ".sample.", ".template.")
    )


def forbidden_file(path: PurePosixPath) -> str | None:
    name = path.name.lower()
    if example_name(name):
        # Examples are text configuration, never disguised signing keys/binaries.
        suffixes = PurePosixPath(name).suffixes
        if not any(suffix in FORBIDDEN_SUFFIXES or suffix == ".jar" for suffix in suffixes):
            return None
        return "signing key, binary, backup or database cannot be disguised as an example"
    if name in FORBIDDEN_NAMES:
        return "local configuration or credential file"
    if name == ".env" or name.startswith(".env."):
        return "non-example environment file"
    if re.search(r"(?:firebase-adminsdk|service[-_]account).*\.json\Z", name):
        return "service-account credential file"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "generated artifact, signing key, log, backup or database"
    if path.suffix.lower() == ".jar" and not (
        path.as_posix() == "android/gradle/wrapper/gradle-wrapper.jar"
        or path.as_posix().endswith("/android/gradle/wrapper/gradle-wrapper.jar")
    ):
        return "generated binary archive"
    return None


def safe_relative(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        return None
    return path


def obvious_fixture(value: str) -> bool:
    """Only suppress recognizable placeholders, never an entire test directory."""
    lower = value.lower()
    if any(marker in lower for marker in ("example", "placeholder", "changeme", "dummy", "synthetic")):
        return True
    body = re.sub(r"^(?:gh[pousr]_|github_pat_|sk-(?:(?:proj|svcacct|ant)-)?|xox[baprs]-|AIza|AKIA|ASIA)", "", value)
    compact = re.sub(r"[^A-Za-z0-9]", "", body)
    if compact and len(set(compact)) <= 2:
        return True
    for sequence in ("0123456789", "1234567890", "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if len(compact) >= 20 and compact in sequence * (len(compact) // len(sequence) + 2):
            return True
    return False


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def has_symlink_component(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def scan_tree(root: Path, failures: list[tuple[str, int, str]]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory, subdirs, names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        retained = []
        for name in sorted(subdirs):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                failures.append((relative, 0, "symlink directory"))
            elif name == ".git":
                continue
            elif name.lower() in FORBIDDEN_DIRS:
                failures.append((relative, 0, "forbidden local/generated directory"))
            else:
                retained.append(name)
        subdirs[:] = retained
        for name in sorted(names):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if name == ".git" and directory_path == root:
                continue
            if path.is_symlink():
                failures.append((relative, 0, "symlink file"))
                continue
            if not path.is_file():
                failures.append((relative, 0, "non-regular file"))
                continue
            forbidden = forbidden_file(PurePosixPath(relative))
            if forbidden:
                # Do not open local credentials or user databases to inspect them.
                failures.append((relative, 0, forbidden))
                continue
            if relative != MANIFEST_NAME:
                files[relative] = path
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            except OSError:
                failures.append((relative, 0, "file could not be read"))
                continue
            for label, pattern in SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    if not obvious_fixture(match.group(0)):
                        line = content.count("\n", 0, match.start()) + 1
                        failures.append((relative, line, label))
    return files


def check_manifest(root: Path, files: dict[str, Path], failures: list[tuple[str, int, str]], source_root: Path | None) -> int:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        failures.append((MANIFEST_NAME, 0, "manifest is missing or is not a regular file"))
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        failures.append((MANIFEST_NAME, 0, "manifest could not be parsed"))
        return 0
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        failures.append((MANIFEST_NAME, 0, "expected schema_version 1"))
        return 0
    records = manifest.get("exported_files")
    if not isinstance(records, list):
        failures.append((MANIFEST_NAME, 0, "exported_files must be an array"))
        return 0
    expected: set[str] = set()
    checked = 0
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            failures.append((MANIFEST_NAME, 0, f"record {index} must be an object"))
            continue
        relative = safe_relative(record.get("path"))
        if relative is None or relative.as_posix() == MANIFEST_NAME:
            failures.append((MANIFEST_NAME, 0, f"record {index} has an invalid export path"))
            continue
        name = relative.as_posix()
        if name in expected:
            failures.append((name, 0, "duplicate manifest record"))
            continue
        expected.add(name)
        if not isinstance(record.get("kind"), str) or record["kind"] not in {"copied", "adapted", "generated"}:
            failures.append((name, 0, "manifest kind must be copied, adapted or generated"))
        expected_hash = record.get("sha256")
        if not isinstance(expected_hash, str) or SHA256.fullmatch(expected_hash) is None:
            failures.append((name, 0, "manifest sha256 must be a lowercase SHA256 digest"))
        elif name not in files:
            failures.append((name, 0, "manifest file is missing or forbidden"))
        else:
            try:
                if digest(files[name]) != expected_hash:
                    failures.append((name, 0, "export hash differs from manifest"))
                else:
                    checked += 1
            except OSError:
                failures.append((name, 0, "export could not be hashed"))
        source = record.get("source")
        source_hash = record.get("source_sha256")
        if source is None and source_hash is None:
            continue
        source_relative = safe_relative(source)
        if source_relative is None or not isinstance(source_hash, str) or SHA256.fullmatch(source_hash) is None:
            failures.append((name, 0, "source and source_sha256 must be a valid pair"))
            continue
        if record.get("kind") == "copied" and expected_hash != source_hash:
            failures.append((name, 0, "copied file has different export and source hashes"))
        source_forbidden_dirs = FORBIDDEN_DIRS - {"artifacts"} if source_relative.as_posix() in ALLOWED_SOURCE_ARTIFACTS else FORBIDDEN_DIRS
        if forbidden_file(source_relative) or any(part.lower() in source_forbidden_dirs or part == ".git" for part in source_relative.parts):
            failures.append((name, 0, "manifest source points to forbidden local material"))
            continue
        if source_root is not None:
            source_path = source_root.joinpath(*source_relative.parts)
            if has_symlink_component(source_root, source_relative) or not source_path.is_file():
                failures.append((name, 0, "source is missing or has a symlink component"))
            else:
                try:
                    if digest(source_path) != source_hash:
                        failures.append((name, 0, "source hash differs from manifest"))
                except OSError:
                    failures.append((name, 0, "source could not be hashed"))
    for name in sorted(set(files) - expected):
        failures.append((name, 0, "file is not listed in manifest"))
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="public tree to check")
    parser.add_argument("--verify-sources", action="store_true", help="also verify hashes of original monorepo files")
    parser.add_argument("--source-root", type=Path, help="monorepo root; defaults to the public tree's parent")
    args = parser.parse_args()
    if args.source_root is not None and not args.verify_sources:
        parser.error("--source-root requires --verify-sources")
    root = args.root.resolve()
    if not root.is_dir():
        print("FAIL: public root is not a directory", file=sys.stderr)
        return 1
    source_root = (args.source_root or root.parent).resolve() if args.verify_sources else None
    failures: list[tuple[str, int, str]] = []
    files = scan_tree(root, failures)
    checked = check_manifest(root, files, failures, source_root)
    if failures:
        for path, line, reason in sorted(set(failures)):
            location = f"{path}:{line}" if line else path
            print(f"FAIL {location}: {reason}")
        print(f"FAILED: {len(set(failures))} finding(s). Secret values are never printed.")
        return 1
    source_status = "; original source hashes also verified" if source_root is not None else ""
    print(f"OK: {checked} exported files match the manifest; no blocked files or recognized secret formats{source_status}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
