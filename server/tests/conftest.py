"""Keep normal export tests independent of production settings and services."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "synthetic-export-test-secret")

