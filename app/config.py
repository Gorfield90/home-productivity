"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass, field


def _parse_users(raw: str) -> list[tuple[str, str]]:
    """Parse HP_USERS="pedro:Pedro,maguy:Maguy" into [(username, display_name)]."""
    users = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        username, _, display = item.partition(":")
        users.append((username.strip(), (display or username).strip()))
    return users


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("HP_DATABASE_URL", "sqlite:///./home.db")
    )
    # Header set by the Authentik forwardAuth outpost behind Traefik.
    auth_header: str = field(
        default_factory=lambda: os.environ.get("HP_AUTH_HEADER", "X-authentik-username")
    )
    # Local development only: act as this user when the auth header is absent.
    dev_user: str | None = field(default_factory=lambda: os.environ.get("HP_DEV_USER") or None)
    # Users created on startup if missing: "username:Display Name,..."
    users: list[tuple[str, str]] = field(
        default_factory=lambda: _parse_users(os.environ.get("HP_USERS", ""))
    )
    # Image version, baked in at build time (e.g. "sha-690b6aa").
    version: str = field(default_factory=lambda: os.environ.get("HP_VERSION", "dev"))


def get_settings() -> Settings:
    return Settings()
