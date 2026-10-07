import pytest
from redis.exceptions import RedisError

from app.config import Settings
from app.redis import check_redis, create_redis_client
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.integration


async def test_redis_roundtrip(infra_settings: Settings) -> None:
    client = create_redis_client(infra_settings)
    try:
        await client.set("phase1:probe", "value", ex=30)
        assert await client.get("phase1:probe") == "value"
        await client.delete("phase1:probe")
    finally:
        await client.aclose()


async def test_check_redis_succeeds_against_live_redis(infra_settings: Settings) -> None:
    client = create_redis_client(infra_settings)
    try:
        await check_redis(client)
    finally:
        await client.aclose()


async def test_check_redis_raises_when_redis_is_unreachable(make_settings: SettingsFactory) -> None:
    client = create_redis_client(make_settings())
    try:
        with pytest.raises(RedisError):
            await check_redis(client)
    finally:
        await client.aclose()
