from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError

router = APIRouter()


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict:
    if not request.app.state.ready:
        response.status_code = 503
        return {"status": "unavailable"}
    bus = request.app.state.bus
    heartbeat = request.app.state.heartbeat
    if not bus.is_ready or heartbeat.done():
        response.status_code = 503
        return {"status": "unavailable", "dependency": "event_workers"}
    try:
        await request.app.state.redis.ping()
    except RedisError:
        response.status_code = 503
        return {"status": "unavailable", "dependency": "redis"}
    return {"status": "ok"}


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return Response(request.app.state.metrics.render_prometheus(), media_type="text/plain; version=0.0.4")
