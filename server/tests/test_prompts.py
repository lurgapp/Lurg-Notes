from datetime import datetime, timezone

from app.prompts import current_time_context


def test_current_time_context_uses_moscow_calendar_day():
    value = current_time_context(datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc))
    assert "2026-08-14 12:30" in value
    assert "Europe/Moscow" in value
