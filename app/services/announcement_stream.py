from __future__ import annotations

import asyncio
import json
from typing import Any

from redis.asyncio.client import PubSub

from app.utils.redis_client import get_redis_client


CHANNEL_PREFIX = "announcements"


def channel_for_org(org_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{org_id}"


async def publish_announcement(org_id: str, payload: dict[str, Any]) -> None:
    redis = get_redis_client()
    await redis.publish(channel_for_org(org_id), json.dumps(payload))


async def subscribe(channel: str) -> PubSub:
    redis = get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    return pubsub


async def unsubscribe(pubsub: PubSub, channel: str) -> None:
    try:
        # Redis/network blips should not block app shutdown/reload.
        await asyncio.wait_for(pubsub.unsubscribe(channel), timeout=2.0)
    finally:
        try:
            await asyncio.wait_for(pubsub.close(), timeout=2.0)
        except Exception:
            pass
