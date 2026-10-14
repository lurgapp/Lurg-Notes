from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
Session = async_sessionmaker(engine, expire_on_commit=False)

async def get_db():
    async with Session() as session:
        yield session
