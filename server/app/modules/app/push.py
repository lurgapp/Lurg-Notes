from __future__ import annotations

from pathlib import Path

import firebase_admin
from firebase_admin import credentials

from ...config import settings


def firebase_enabled() -> bool:
    return bool(settings.firebase_service_account_file and Path(settings.firebase_service_account_file).is_file())


def firebase_app():
    if firebase_admin._apps:
        return firebase_admin.get_app()
    if not firebase_enabled():
        raise RuntimeError("FCM не настроен")
    return firebase_admin.initialize_app(credentials.Certificate(settings.firebase_service_account_file), options={"httpTimeout": 10})

