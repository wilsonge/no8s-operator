"""
Authentication and authorisation utilities.

Provides JWT token creation/validation and FastAPI dependency functions
for role-based access control.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


class AuthManager:
    """Handles password hashing and JWT token operations."""

    def __init__(self, jwt_secret_key: str, jwt_expiry_hours: int = 24):
        if not jwt_secret_key:
            raise ValueError("JWT_SECRET_KEY must not be empty")
        self._secret = jwt_secret_key
        self._expiry_hours = jwt_expiry_hours

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------

    def hash_password(self, plain: str) -> str:
        """Return a bcrypt hash of *plain*."""
        return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def verify_password(self, plain: str, hashed: str) -> bool:
        """Return True if *plain* matches *hashed*."""
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

    # ------------------------------------------------------------------
    # JWT helpers
    # ------------------------------------------------------------------

    def create_token(self, user: dict) -> str:
        """Create a signed JWT for *user*.

        Payload fields: sub, username, is_admin, source, custom_role_id, exp.
        """
        exp = datetime.now(timezone.utc) + timedelta(hours=self._expiry_hours)
        payload = {
            "sub": str(user["id"]),
            "username": user["username"],
            "is_admin": bool(user.get("is_admin", False)),
            "source": user["source"],
            "custom_role_id": user.get("custom_role_id"),
            "exp": exp,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def decode_token(self, token: str) -> dict:
        """Decode and verify a JWT.

        Raises:
            HTTPException(401): if the token is missing, expired, or invalid.
        """
        try:
            return jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton set by main.py so dependency functions can reach it.
# ---------------------------------------------------------------------------

_auth_manager: Optional[AuthManager] = None


def set_auth_manager(manager: AuthManager) -> None:
    global _auth_manager
    _auth_manager = manager


def get_auth_manager() -> AuthManager:
    if _auth_manager is None:
        raise RuntimeError("AuthManager not initialised")
    return _auth_manager


# ---------------------------------------------------------------------------
# FastAPI dependency functions
# ---------------------------------------------------------------------------


def _extract_bearer(request: Request) -> str:
    """Pull the Bearer token from the Authorization header."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    return header[len("Bearer ") :]


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency — validates bearer token, returns decoded payload."""
    token = _extract_bearer(request)
    return get_auth_manager().decode_token(token)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency — requires is_admin=True."""
    if user.get("is_admin") is not True:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


# ---------------------------------------------------------------------------
# Config loader (convenience, used by main.py)
# ---------------------------------------------------------------------------


def auth_manager_from_env() -> AuthManager:
    """Create an AuthManager from environment variables."""
    secret = os.getenv("JWT_SECRET_KEY", "")
    if not secret:
        raise ValueError("JWT_SECRET_KEY environment variable must be set")
    expiry = int(os.getenv("JWT_EXPIRY_HOURS", "24"))
    return AuthManager(jwt_secret_key=secret, jwt_expiry_hours=expiry)


async def check_resource_permission(
    user: dict,
    db,
    resource_type_name: str,
    resource_type_version: str,
    operation: str,
) -> bool:
    """Return True if *user* may perform *operation* on the given resource type.

    Admins always pass. Non-admin users need a matching custom role permission.
    """
    if user.get("is_admin"):
        return True
    custom_role_id = user.get("custom_role_id")
    if not custom_role_id:
        return False
    permissions = await db.get_custom_role_permissions(custom_role_id)
    for perm in permissions:
        name_ok = perm["resource_type_name"] in ("*", resource_type_name)
        version_ok = perm["resource_type_version"] in ("*", resource_type_version)
        if name_ok and version_ok and operation in perm["operations"]:
            return True
    return False


async def check_system_permission(user: dict, db, permission: str) -> bool:
    """Return True if *user* may access a system-level resource.

    Admins always pass. Non-admins need the permission in their custom role's
    system_permissions list.
    """
    if user.get("is_admin"):
        return True
    custom_role_id = user.get("custom_role_id")
    if not custom_role_id:
        return False
    role = await db.get_custom_role(custom_role_id)
    return role is not None and permission in role.get("system_permissions", [])
