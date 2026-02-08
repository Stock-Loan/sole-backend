"""Global test fixtures.

Disables SlowAPI rate limiting so tests don't require a running Redis instance.
"""

import pytest

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.main import app


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Replace Redis-backed limiter with in-memory limiter for all tests."""
    original = app.state.limiter
    app.state.limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",
    )
    yield
    app.state.limiter = original
