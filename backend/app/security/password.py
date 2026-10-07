import asyncio

import bcrypt

MAX_PASSWORD_BYTES = 72  # bcrypt ignores input beyond 72 bytes; longer passwords are rejected.
_DUMMY_PASSWORD = b"timing-equalisation-placeholder"


class PasswordHasher:
    """bcrypt hashing. The CPU-bound work runs in a thread so the event loop is not blocked."""

    def __init__(self, cost_factor: int) -> None:
        self._cost_factor = cost_factor
        self._dummy_hash = bcrypt.hashpw(_DUMMY_PASSWORD, bcrypt.gensalt(rounds=cost_factor))

    def hash_sync(self, password: str) -> str:
        encoded = password.encode("utf-8")
        if len(encoded) > MAX_PASSWORD_BYTES:
            raise ValueError("password exceeds the bcrypt input limit")
        return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=self._cost_factor)).decode("ascii")

    def verify_sync(self, password: str, password_hash: str | None) -> bool:
        """Verify a password; always spends one bcrypt computation, even for unknown accounts."""
        encoded = password.encode("utf-8")
        target = password_hash.encode("ascii") if password_hash else self._dummy_hash
        if len(encoded) > MAX_PASSWORD_BYTES:
            bcrypt.checkpw(_DUMMY_PASSWORD, self._dummy_hash)
            return False
        try:
            matches = bcrypt.checkpw(encoded, target)
        except ValueError:
            return False
        return matches and password_hash is not None

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self.hash_sync, password)

    async def verify(self, password: str, password_hash: str | None) -> bool:
        return await asyncio.to_thread(self.verify_sync, password, password_hash)
