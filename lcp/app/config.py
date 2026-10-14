from functools import lru_cache
from uuid import uuid4

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    lcp_gateway_id: str = f"gw_{uuid4().hex}"
    lcp_protocol_version: int = 1
    lcp_max_frame_bytes: int = 65_536
    lcp_max_inflight_requests: int = 32
    lcp_hello_timeout_seconds: float = 10
    lcp_auth_timeout_seconds: float = 30
    lcp_replay_max_events: int = 10_000
    lcp_replay_max_age_seconds: int = 600
    lcp_session_ttl_seconds: int = 86_400
    lcp_heartbeat_seconds: int = 5
    lcp_send_timeout_seconds: float = 5
    lcp_delivery_poll_seconds: float = 0.1
    lcp_delivery_batch_size: int = 128
    lcp_max_connections: int = 1000
    lcp_max_subscriptions: int = 32
    lcp_idempotency_processing_seconds: int = 30
    lcp_event_stream_max_events: int = 100_000
    lcp_legacy_pubsub_enabled: bool = True
    redis_url: str = "redis://redis:6379/0"
    auth_jwt_secret: str = ""
    auth_jwt_issuer: str = "https://auth.lurg.pro"
    auth_jwt_audience: str = "lurg"
    auth_session_status_url: str = "http://backend:8000/v1/auth/internal/session-status"
    lcp_auth_check_cache_seconds: float = 5
    nearby_service_url: str = "http://nearby:8000/api/v1"
    notes_service_url: str = "http://notes:8000"
    core_service_url: str = "http://core:8000"
    graph_service_url: str = "http://graph:8000"
    lcp_internal_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
