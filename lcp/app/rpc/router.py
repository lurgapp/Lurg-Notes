from dataclasses import dataclass

from ..config import Settings
from .client import RPCClient
from .methods import MethodMetadata


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    user_id: str | None
    session_id: str
    auth_kind: str


class ServiceRouter:
    def __init__(self, client: RPCClient, settings: Settings) -> None:
        self.client, self.settings = client, settings
        self.routes = {
            "nearby": settings.nearby_service_url,
            "notes": settings.notes_service_url,
            "profile": settings.core_service_url,
            "graph": settings.graph_service_url,
        }

    async def route(
        self, method: str, context: PrincipalContext, payload: dict, metadata: MethodMetadata, request_id: str,
        *, idempotency_key: str | None = None, generation: int | None = None, gateway_id: str | None = None,
    ) -> dict:
        service = method.split(".", 1)[0]
        base_url = self.routes.get(service)
        if not base_url:
            return {
                "ok": False,
                "error": {"code": "LCP_METHOD_NOT_FOUND", "message": "Unknown method", "retryable": False},
            }
        return await self.client.post(
            f"{base_url.rstrip('/')}/internal/lcp/rpc",
            {
                "method": method,
                "principal": {
                    "user_id": context.user_id,
                    "session_id": context.session_id,
                    "auth_kind": context.auth_kind,
                },
                "request": {"id": request_id},
                "payload": payload,
            },
            metadata.timeout_seconds,
            self.settings.lcp_internal_token,
            extra_headers={key: str(value) for key, value in {
                "X-LCP-Idempotency-Key": idempotency_key,
                "X-LCP-Session-Generation": generation,
                "X-LCP-Gateway": gateway_id,
            }.items() if value is not None},
        )
