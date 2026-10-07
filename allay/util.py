"""Small shared helpers: logging, json loading, text formatting, emoji."""

import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger("allay")


class _RedactFilter(logging.Filter):
    """Keeps the bot token out of logs (it is part of every Telegram API URL)."""

    def __init__(self, secret):
        super().__init__()
        self.secret = secret

    def filter(self, record):
        record.msg = str(record.getMessage()).replace(self.secret, "***")
        record.args = None
        # tracebacks too: requests exceptions carry the full API URL
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = record.exc_text.replace(self.secret, "***")
        return True


def setup_logging(secret):
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    handler.addFilter(_RedactFilter(secret))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# name -> (fallback emoji, custom_emoji_id or None)
EMOJI = {
    # new player announcement
    "welcome": ("💌", "5307974696136889336"),
    "whitelist": ("✅", "5307564088673455989"),
    "lock": ("🔒", "5307989333385433249"),
    "pickaxe": ("⛏️", "5278459663698912652"),
    # join
    "join": ("🌀", "5048801977060819786"),
    "online": ("👤", "5462957495796382139"),
    "first_join": ("🔔", "5202191064680642242"),
    # commands (plain emoji for now)
    "world": ("🌍", "5321551092054500750"),
    "offline": ("🔴", "4981503065052939394"),
    "speed": ("⚡", None),
    "uptime": ("⏱", "4904524469285946412"),
    "disk": ("💾", None),
    "backup": ("🗄", None),
    "p_online": ("🟢", "5307588930764297000"),
    "p_seen": ("⚪", "5032980112111305692"),
    "p_never": ("⚫", "4902235874487436351"),
    "leave": ("🚪", "5071095554566521757"),
    "death": ("💀", None),
    # advancements (per-advancement icons live in advancements.py)
    "adv_many": ("🏅", "5256239066277500042"),
    "adv_first": ("🥇", "5801004944211317728"),
    # /top, /stats, /world
    "top": ("🏆", "5422546251587527850"),
    "top1": ("🥇", "5801004944211317728"),
    "top2": ("🥈", None),
    "top3": ("🥉", None),
    "stats": ("📊", None),
    "distance": ("🗺", "5071608743028851679"),
    "mined": ("⛏️", "6280750854144266316"),
    "diamond": ("💎", "5773770578062216835"),
    "kills": ("⚔️", "6278389670103491005"),
    "advancements": ("🏅", None),
    "favorite": ("❤️", "5774092580350332850"),
    "day": ("☀️", None),
    "night": ("🌙", None),
    "dusk": ("🌅", None),
    "difficulty": ("🎚", None),
    "version": ("🧩", None),
}


def emoji(fallback, custom_id=None):
    return (
        f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
        if custom_id
        else fallback
    )


def e(name):
    return emoji(*EMOJI[name])


def fmt_size(n):
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}" if i >= 2 else f"{n:.0f} {units[i]}"


def fmt_int(n):
    """12345 -> '12 345'"""
    return f"{int(n):,}".replace(",", " ")


def fmt_distance(cm):
    m = cm / 100
    return f"{m / 1000:.1f} км" if m >= 1000 else f"{m:.0f} м"


def fmt_duration(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def fmt_seen(ts, tz):
    now = datetime.now(tz)
    t = datetime.fromtimestamp(ts, tz)
    delta = now - t
    if delta < timedelta(minutes=1):
        return "только что"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} мин назад"
    if t.date() == now.date():
        return f"сегодня в {t:%H:%M}"
    if t.date() == (now - timedelta(days=1)).date():
        return f"вчера в {t:%H:%M}"
    return f"{t:%d.%m} в {t:%H:%M}"
