from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import deps
from app.core.settings import settings


def test_enforce_inactivity_allows_recent_activity(monkeypatch):
    monkeypatch.setattr(settings, "session_timeout_minutes", 30)
    now = datetime.now(timezone.utc)
    deps.enforce_inactivity(now - timedelta(minutes=10), now)


def test_enforce_inactivity_raises_when_expired(monkeypatch):
    monkeypatch.setattr(settings, "session_timeout_minutes", 30)
    now = datetime.now(timezone.utc)
    with pytest.raises(HTTPException) as exc:
        deps.enforce_inactivity(now - timedelta(minutes=45), now)
    assert exc.value.status_code == 401
    assert "Session expired" in exc.value.detail
