"""Service configuration, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


class ConfigError(Exception):
    pass


def _str(name, default=None):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if default is None:
        raise ConfigError(f"required env var {name} is not set")
    return default


def _int(name, default=None):
    raw = _str(name, None if default is None else str(default))
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"env var {name} must be an integer, got {raw!r}") from None


@dataclass(frozen=True)
class Config:
    tg_token: str
    chat_id: int
    channel_id: int | None
    admin_id: int | None
    proxy: str | None
    rcon_host: str
    rcon_port: int
    rcon_password: str
    tz: ZoneInfo
    server_name: str
    hidden: frozenset[str]
    data_dir: Path
    backups_dir: Path
    players_file: Path
    lang_dir: Path

    @classmethod
    def from_env(cls):
        hidden = _str("ALLAY_HIDDEN", "admin")
        return cls(
            tg_token=_str("TG_BOT_TOKEN"),
            chat_id=_int("TG_CHAT_ID"),
            channel_id=_int("TG_CHANNEL_ID") if os.environ.get("TG_CHANNEL_ID", "").strip() else None,
            admin_id=_int("TG_ADMIN_ID") if os.environ.get("TG_ADMIN_ID", "").strip() else None,
            proxy=os.environ.get("TG_PROXY", "").strip() or None,
            rcon_host=_str("RCON_HOST", "mc"),
            rcon_port=_int("RCON_PORT", 25575),
            rcon_password=_str("RCON_PASSWORD"),
            tz=ZoneInfo(_str("TZ", "Europe/Moscow")),
            server_name=_str("SERVER_NAME", "The Brave New World"),
            hidden=frozenset(n.strip().lower() for n in hidden.split(",") if n.strip()),
            # paths inside the container; docker-compose.yml mounts host dirs here
            data_dir=Path(_str("DATA_DIR", "/data")),
            backups_dir=Path(_str("BACKUPS_DIR", "/backups")),
            players_file=Path(_str("PLAYERS_FILE", "/state/players.json")),
            lang_dir=Path(_str("LANG_DIR", "/app/lang")),
        )
