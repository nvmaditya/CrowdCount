"""Authentication: password hashing, JWT access tokens and the user store.

- Passwords are hashed with bcrypt. Hashes written by CrowdCount 1.x (passlib/bcrypt,
  ``$2b$``) still verify.
- Tokens are HS256 JWTs with a ``jti`` so logout can revoke them. Revocations live in
  memory, which is fine for a single server process.
- The signing secret comes from ``CROWDCOUNT_JWT_SECRET``; otherwise one is generated
  once and kept in ``<data_dir>/.jwt_secret`` (owner-only permissions).
- There is no built-in default password. The first start creates ``admin`` with
  ``CROWDCOUNT_ADMIN_PASSWORD``, or a random password printed once to the log.
"""

from __future__ import annotations

import functools
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import bcrypt
import jwt

from crowdcount.server.models import TokenData, UserInDB, UserRole, utcnow
from crowdcount.server.storage import JsonStore, atomic_write_text

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
DEFAULT_TOKEN_TTL = timedelta(hours=24)
INITIAL_ADMIN_USERNAME = "admin"
_V1_DEFAULT_PASSWORD = "admin123"


@functools.cache
def _dummy_hash() -> str:
    """Checked when a username doesn't exist, so failed logins take the same time either way."""
    return bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode("ascii")


class UserExistsError(ValueError):
    pass


class LastAdminError(ValueError):
    pass


# ------------------------------------------------------------------ passwords


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:  # malformed hash, or a password longer than bcrypt accepts
        return False


# ------------------------------------------------------------------ tokens


def load_or_create_secret(data_dir: Path, env: Mapping[str, str]) -> str:
    """Return the JWT signing secret from the environment or ``<data_dir>/.jwt_secret``."""
    for var in ("CROWDCOUNT_JWT_SECRET", "JWT_SECRET_KEY"):  # JWT_SECRET_KEY: 1.x name
        if env.get(var):
            if len(env[var]) < 32:
                logger.warning("%s is shorter than 32 characters; use a long random value", var)
            return env[var]
    path = data_dir / ".jwt_secret"
    if path.is_file():
        secret = path.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    secret = secrets.token_urlsafe(48)
    atomic_write_text(path, secret + "\n", mode=0o600)
    logger.info("Generated a JWT signing secret in %s", path)
    return secret


class TokenService:
    """Issue, decode and revoke access tokens."""

    def __init__(self, secret: str, ttl: timedelta = DEFAULT_TOKEN_TTL) -> None:
        self._secret = secret
        self.ttl = ttl
        self._revoked: dict[str, float] = {}  # jti -> expiry (unix time)
        self._lock = threading.Lock()

    def issue(self, user: UserInDB) -> tuple[str, int]:
        """Return ``(token, expires_in_seconds)`` for ``user``."""
        now = datetime.now(timezone.utc)
        claims = {
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "iat": now,
            "exp": now + self.ttl,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM), int(self.ttl.total_seconds())

    def _claims(self, token: str, *, verify_exp: bool = True) -> dict[str, Any] | None:
        try:
            return jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                options={"require": ["sub", "exp", "jti"], "verify_exp": verify_exp},
            )
        except jwt.PyJWTError:
            return None

    def decode(self, token: str) -> TokenData | None:
        """Return the token's claims, or ``None`` if invalid, expired or revoked."""
        claims = self._claims(token)
        if claims is None:
            return None
        with self._lock:
            if claims["jti"] in self._revoked:
                return None
        try:
            return TokenData(
                user_id=claims["sub"],
                username=claims["username"],
                role=UserRole(claims["role"]),
                exp=datetime.fromtimestamp(claims["exp"], timezone.utc),
                jti=claims["jti"],
            )
        except (KeyError, ValueError):
            return None

    def revoke(self, token: str) -> None:
        claims = self._claims(token, verify_exp=False)
        if claims is None:
            return
        now = time.time()
        with self._lock:
            self._revoked = {j: exp for j, exp in self._revoked.items() if exp > now}
            self._revoked[claims["jti"]] = float(claims["exp"])


# ------------------------------------------------------------------ users


class UserStore:
    """Users persisted in ``users.json``."""

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    def _users(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return data.setdefault("users", [])

    def all(self) -> list[UserInDB]:
        return [UserInDB(**u) for u in self._store.read().get("users", [])]

    def get(self, user_id: str) -> UserInDB | None:
        return next((u for u in self.all() if u.id == user_id), None)

    def get_by_username(self, username: str) -> UserInDB | None:
        return next((u for u in self.all() if u.username == username), None)

    def count(self) -> int:
        return len(self._store.read().get("users", []))

    def create(self, username: str, password: str, role: UserRole = UserRole.USER) -> UserInDB:
        user = UserInDB(username=username, password_hash=hash_password(password), role=role)

        def add(data: dict[str, Any]) -> None:
            users = self._users(data)
            if any(u.get("username") == username for u in users):
                raise UserExistsError(f"user '{username}' already exists")
            users.append(user.model_dump(mode="json"))

        self._store.update(add)
        return user

    def delete(self, user_id: str) -> UserInDB | None:
        """Delete a user. Refuses to remove the last admin."""

        def remove(data: dict[str, Any]) -> UserInDB | None:
            users = self._users(data)
            target = next((u for u in users if u.get("id") == user_id), None)
            if target is None:
                return None
            admins = [u for u in users if u.get("role") == UserRole.ADMIN.value]
            if target.get("role") == UserRole.ADMIN.value and len(admins) == 1:
                raise LastAdminError("cannot delete the last admin account")
            users.remove(target)
            return UserInDB(**target)

        return self._store.update(remove)

    def set_password(self, username: str, password: str) -> bool:
        password_hash = hash_password(password)

        def change(data: dict[str, Any]) -> bool:
            for u in self._users(data):
                if u.get("username") == username:
                    u["password_hash"] = password_hash
                    return True
            return False

        return self._store.update(change)

    def touch_login(self, user_id: str) -> None:
        def touch(data: dict[str, Any]) -> None:
            for u in self._users(data):
                if u.get("id") == user_id:
                    u["last_login"] = utcnow().isoformat()

        self._store.update(touch)

    def authenticate(self, username: str, password: str) -> UserInDB | None:
        user = self.get_by_username(username)
        if user is None:
            verify_password(password, _dummy_hash())
            return None
        return user if verify_password(password, user.password_hash) else None

    def ensure_admin(self, env: Mapping[str, str]) -> str | None:
        """Create the first admin if there are no users.

        Returns the generated password if one was generated (so it can be shown once),
        else ``None``.
        """
        if self.count():
            admin = self.get_by_username(INITIAL_ADMIN_USERNAME)
            if admin and verify_password(_V1_DEFAULT_PASSWORD, admin.password_hash):
                logger.warning(
                    "The '%s' account still uses the old default password. Change it with "
                    "`crowdcount admin reset-password` or from Settings.",
                    INITIAL_ADMIN_USERNAME,
                )
            return None
        password = env.get("CROWDCOUNT_ADMIN_PASSWORD") or ""
        generated = None
        if not password:
            password = generated = secrets.token_urlsafe(12)
        self.create(INITIAL_ADMIN_USERNAME, password, UserRole.ADMIN)
        return generated
