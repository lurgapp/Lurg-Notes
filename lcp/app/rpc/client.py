import httpx


class RPCClient:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            timeout=httpx.Timeout(connect=2, read=10, write=10, pool=2),
        )

    async def post(
        self, url: str, body: dict, timeout_seconds: float, internal_token: str, *, extra_headers: dict | None = None
    ) -> dict:
        headers = {"X-LCP-Internal-Token": internal_token} if internal_token else {}
        headers.update(extra_headers or {})
        try:
            response = await self.client.post(url, json=body, headers=headers, timeout=timeout_seconds)
        except httpx.HTTPError:
            return {
                "ok": False,
                "error": {
                    "code": "LCP_SERVICE_UNAVAILABLE",
                    "message": "Service unavailable",
                    "retryable": True,
                },
            }
        try:
            value = response.json()
        except ValueError:
            value = None
        if (not isinstance(value, dict) or not isinstance(value.get("ok"), bool)
                or not isinstance(value.get("payload" if value.get("ok") else "error", {}), dict)):
            return {
                "ok": False,
                "error": {
                    "code": "LCP_SERVICE_PROTOCOL",
                    "message": "Invalid service response",
                    "retryable": response.status_code >= 500,
                },
            }
        return value

    async def close(self) -> None:
        await self.client.aclose()
