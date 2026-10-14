from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    ai_provider: str = "openrouter"
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: str = "https://lurg.pro"
    openrouter_app_title: str = "Lurg"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-001"
    search_provider: str = "searxng"
    search_fallback_provider: str = "none"
    searxng_base_url: str = "http://searxng:8080"
    web_search_enabled: bool = True
    web_search_max_results: int = 8
    web_search_timeout_seconds: float = 10
    web_search_safesearch: int = 1
    web_search_cache_ttl_seconds: int = 600
    ai_max_input_chars: int = 48000
    ai_max_output_tokens: int = 1200
    ai_daily_request_limit: int = 200
    lcp_event_bus_redis_url: str = ""
    lcp_internal_token: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "https://api.lurg.pro/connect/github/callback"
    connector_encryption_key: str = ""
    cookie_domain: str = ".lurg.pro"
    cookie_secure: bool = True
    cors_origins: str = "https://lurg.pro,https://auth.lurg.pro,https://graph.lurg.pro,https://core.lurg.pro"
    firebase_service_account_file: str = ""
    push_internal_token: str = ""
    nearby_admin_push_url: str = ""
    nearby_admin_push_token: str = ""
    admin_owner_email: str = ""
    admin_session_secret: str = ""
    admin_allowed_origins: str = "https://admin.lurg.pro"
    admin_session_hours: int = 8
    admin_totp_issuer: str = "Lurg Admin"
    operations_agent_token: str = ""
    # auth.lurg.pro is the shared identity provider.  Services select their
    # mail branding with X-Lurg-App-Name; they never own a password database.
    auth_access_token_minutes: int = 30
    auth_refresh_token_days: int = 30
    auth_otp_hmac_secret: str = ""
    auth_allowed_apps: str = "lurg_notes"
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "Lurg"
    brevo_template_verify_email_id: int | None = None
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
