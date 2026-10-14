"""Transactional mail for the central identity provider.

No code or credential is written to logs.  Brevo receives the service display
name as APP_NAME, so one maintained template works for Nearby, Graph and new
services.
"""
from __future__ import annotations

import httpx
from ...config import settings


class EmailDeliveryError(RuntimeError):
    pass


async def send_verification_code(*, email: str, code: str, app_name: str) -> None:
    if not all((settings.brevo_api_key, settings.brevo_sender_email, settings.brevo_template_verify_email_id)):
        raise EmailDeliveryError("Email delivery is not configured")
    payload = {
        "to": [{"email": email}],
        "sender": {"email": settings.brevo_sender_email, "name": settings.brevo_sender_name},
        "templateId": settings.brevo_template_verify_email_id,
        "params": {"APP_NAME": app_name, "CODE": code, "EXPIRES_MINUTES": 5},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5)) as client:
            response = await client.post("https://api.brevo.com/v3/smtp/email", json=payload, headers={"api-key": settings.brevo_api_key, "accept": "application/json"})
        if not response.is_success:
            raise EmailDeliveryError("Email provider rejected delivery")
    except httpx.HTTPError as exc:
        raise EmailDeliveryError("Email provider is unavailable") from exc
