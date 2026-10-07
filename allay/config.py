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


def _weekday(name, default):
    day = _int(name, default)
    if not 1 <= day <= 7:
        raise ConfigError(f"env var {name} must be 1-7 (Monday-Sunday), got {day}")
    return day


def _clock(name, default):
    raw = _str(name, default)
    try:
        hour, minute = map(int, raw.split(":"))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    except ValueError:
        pass
    raise ConfigError(f"env var {name} must be HH:MM, got {raw!r}")


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
    # admin alerts (sent only when admin_id is set)
    alert_down_minutes: int
    alert_restart_minutes: int
    alert_mspt: int
    alert_lag_minutes: int
    alert_backup_hours: int
    alert_disk_gb: int
    alert_repeat_hours: int
    # weekly digest: day 1-7 (Mon-Sun) and (hour, minute) in tz
    digest_weekday: int
    digest_time: tuple[int, int]

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
            alert_down_minutes=_int("ALERT_DOWN_MINUTES", 2),
            alert_restart_minutes=_int("ALERT_RESTART_MINUTES", 5),
            alert_mspt=_int("ALERT_MSPT", 50),
            alert_lag_minutes=_int("ALERT_LAG_MINUTES", 5),
            alert_backup_hours=_int("ALERT_BACKUP_HOURS", 14),
            alert_disk_gb=_int("ALERT_DISK_GB", 5),
            alert_repeat_hours=_int("ALERT_REPEAT_HOURS", 6),
            digest_weekday=_weekday("DIGEST_WEEKDAY", 7),
            digest_time=_clock("DIGEST_TIME", "20:00"),
        )
