"""
LDAP integration: user search, authentication, and DB sync.

LDAP support is entirely optional — if LDAP_URL is not set the
LDAPSyncManager is still safe to construct and use (is_configured()
returns False).
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LDAPConfig:
    """Configuration for LDAP connectivity, loaded from environment variables."""

    url: Optional[str] = None
    bind_dn: Optional[str] = None
    bind_password: Optional[str] = None
    base_dn: Optional[str] = None
    user_filter: str = "(objectClass=inetOrgPerson)"
    attr_username: str = "uid"
    attr_email: str = "mail"
    attr_display_name: str = "cn"
    sync_interval: int = 0  # 0 = disabled

    @classmethod
    def from_env(cls) -> "LDAPConfig":
        return cls(
            url=os.getenv("LDAP_URL"),
            bind_dn=os.getenv("LDAP_BIND_DN"),
            bind_password=os.getenv("LDAP_BIND_PASSWORD"),
            base_dn=os.getenv("LDAP_BASE_DN"),
            user_filter=os.getenv("LDAP_USER_FILTER", "(objectClass=inetOrgPerson)"),
            attr_username=os.getenv("LDAP_ATTR_USERNAME", "uid"),
            attr_email=os.getenv("LDAP_ATTR_EMAIL", "mail"),
            attr_display_name=os.getenv("LDAP_ATTR_DISPLAY_NAME", "cn"),
            sync_interval=int(os.getenv("LDAP_SYNC_INTERVAL", "0")),
        )


class LDAPSyncManager:
    """Manages LDAP user discovery, authentication, and DB synchronisation."""

    def __init__(self, config: LDAPConfig):
        self._cfg = config

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True only when LDAP_URL has been provided."""
        return bool(self._cfg.url)

    def search_users(self) -> List[Dict[str, Any]]:
        """Bind as the service account and enumerate directory users.

        Returns a list of dicts with keys: dn, uid, email, display_name.
        Raises RuntimeError if LDAP is not configured.
        """
        if not self.is_configured():
            raise RuntimeError("LDAP is not configured")

        from ldap3 import SUBTREE, Connection, Server  # noqa: PLC0415

        server = Server(self._cfg.url)
        conn = Connection(
            server,
            user=self._cfg.bind_dn,
            password=self._cfg.bind_password,
            auto_bind=True,
        )

        attrs = [
            self._cfg.attr_username,
            self._cfg.attr_email,
            self._cfg.attr_display_name,
        ]

        conn.search(
            search_base=self._cfg.base_dn,
            search_filter=self._cfg.user_filter,
            search_scope=SUBTREE,
            attributes=attrs,
        )

        results: List[Dict[str, Any]] = []
        for entry in conn.entries:

            def _get(attr: str) -> Optional[str]:
                try:
                    val = getattr(entry, attr).value
                    return val if isinstance(val, str) else str(val)
                except Exception:
                    return None

            uid = _get(self._cfg.attr_username)
            if not uid:
                continue
            results.append(
                {
                    "dn": entry.entry_dn,
                    "uid": uid,
                    "email": _get(self._cfg.attr_email),
                    "display_name": _get(self._cfg.attr_display_name),
                }
            )

        conn.unbind()
        return results

    def authenticate(self, user_ldap_dn: str, password: str) -> bool:
        """Attempt a bind with *user_ldap_dn* and *password*.

        Returns True on success, False on invalid credentials.
        Propagates other exceptions (network errors, etc.).
        """
        if not self.is_configured():
            return False

        from ldap3 import Connection, Server  # noqa: PLC0415

        server = Server(self._cfg.url)
        try:
            conn = Connection(
                server, user=user_ldap_dn, password=password, auto_bind=True
            )
            conn.unbind()
            return True
        except Exception as exc:
            logger.debug("LDAP bind failed for %s: %s", user_ldap_dn, exc)
            return False

    async def sync_to_db(self, db) -> Dict[str, int]:
        """Upsert all LDAP users into the database.

        Returns a dict with keys: created, updated, total.
        """
        if not self.is_configured():
            return {"created": 0, "updated": 0, "total": 0}

        users = self.search_users()
        created = 0
        updated = 0

        for u in users:
            result = await db.upsert_ldap_user(
                ldap_dn=u["dn"],
                ldap_uid=u["uid"],
                username=u["uid"],
                email=u.get("email"),
                display_name=u.get("display_name"),
            )
            if result.get("_created"):
                created += 1
            else:
                updated += 1

        logger.info(
            "LDAP sync complete: %d created, %d updated, %d total",
            created,
            updated,
            len(users),
        )
        return {"created": created, "updated": updated, "total": len(users)}
