"""Initialize schema.sql only in an empty PostgreSQL schema.

Run once before `alembic upgrade head`. Docker Compose initializes its fresh
PostgreSQL volume with the same SQL file, so this helper is for manual setup.
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import engine


async def initialize():
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('lurg-notes-schema-init',0))"))
            tables = await connection.scalar(text("""SELECT count(*) FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_type='BASE TABLE'"""))
            if tables:
                raise RuntimeError("Refusing initialization: the current database schema is not empty. Run Alembic separately for an existing versioned database.")
            raw = (await connection.get_raw_connection()).driver_connection
            await raw.execute((ROOT / "schema.sql").read_text())
        print("Base schema initialized. Run: alembic upgrade head")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(initialize())
