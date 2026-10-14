from contextlib import asynccontextmanager
from time import perf_counter


@asynccontextmanager
async def timed():
    started = perf_counter()
    try:
        yield lambda: int((perf_counter() - started) * 1000)
    finally:
        pass
