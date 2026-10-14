import hashlib
import secrets


def generate_resume_token() -> tuple[str, str]:
    raw_token = f"lcp_rt_{secrets.token_urlsafe(32)}"
    return raw_token, hashlib.sha256(raw_token.encode()).hexdigest()


def hash_resume_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
