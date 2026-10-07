import pytest

from app.security.password import MAX_PASSWORD_BYTES, PasswordHasher

hasher = PasswordHasher(cost_factor=4)


def test_hash_is_bcrypt_with_configured_cost_and_not_plaintext() -> None:
    hashed = hasher.hash_sync("Str0ngPassw0rd")

    assert hashed.startswith("$2b$04$")
    assert "Str0ngPassw0rd" not in hashed


def test_same_password_hashes_differently_each_time() -> None:
    assert hasher.hash_sync("Str0ngPassw0rd") != hasher.hash_sync("Str0ngPassw0rd")


def test_verify_accepts_correct_and_rejects_wrong_password() -> None:
    hashed = hasher.hash_sync("Str0ngPassw0rd")

    assert hasher.verify_sync("Str0ngPassw0rd", hashed) is True
    assert hasher.verify_sync("Str0ngPassw0rD", hashed) is False


def test_verify_without_a_stored_hash_is_always_false() -> None:
    assert hasher.verify_sync("anything", None) is False
    assert hasher.verify_sync("timing-equalisation-placeholder", None) is False


def test_verify_returns_false_for_corrupt_hash() -> None:
    assert hasher.verify_sync("Str0ngPassw0rd", "not-a-bcrypt-hash") is False


def test_passwords_longer_than_bcrypt_limit_are_rejected() -> None:
    too_long = "A1" + "a" * MAX_PASSWORD_BYTES

    with pytest.raises(ValueError, match="limit"):
        hasher.hash_sync(too_long)
    assert hasher.verify_sync(too_long, hasher.hash_sync("Str0ngPassw0rd")) is False


def test_multibyte_passwords_are_measured_in_bytes() -> None:
    password = "Aa1" + "é" * 35  # 3 + 70 bytes = 73 bytes

    with pytest.raises(ValueError, match="limit"):
        hasher.hash_sync(password)


async def test_async_hash_and_verify_round_trip() -> None:
    hashed = await hasher.hash("Str0ngPassw0rd")

    assert await hasher.verify("Str0ngPassw0rd", hashed) is True
    assert await hasher.verify("wrong", hashed) is False
