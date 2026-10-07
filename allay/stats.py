"""Player statistics from world/players/stats/<uuid>.json."""

from .util import fmt_distance, fmt_duration, fmt_int

TICKS_PER_SECOND = 20
# *_one_cm stats that aren't travel: creative flight and falling
NOT_TRAVEL = {"minecraft:fly_one_cm", "minecraft:fall_one_cm"}
DIAMOND_ORES = ("minecraft:diamond_ore", "minecraft:deepslate_diamond_ore")


class PlayerStats:
    def __init__(self, raw):
        stats = raw.get("stats") if isinstance(raw, dict) else None
        self._stats = stats if isinstance(stats, dict) else {}

    def category(self, name):
        """{"minecraft:stone": 12, ...} for "mined", "killed", "custom", ..."""
        values = self._stats.get(f"minecraft:{name}")
        return values if isinstance(values, dict) else {}

    def custom(self, key):
        return self.category("custom").get(f"minecraft:{key}", 0)

    @property
    def play_seconds(self):
        return self.custom("play_time") / TICKS_PER_SECOND

    @property
    def since_death_seconds(self):
        return self.custom("time_since_death") / TICKS_PER_SECOND

    @property
    def deaths(self):
        return self.custom("deaths")

    @property
    def mob_kills(self):
        return self.custom("mob_kills")

    @property
    def player_kills(self):
        return self.custom("player_kills")

    @property
    def distance_cm(self):
        return sum(v for k, v in self.category("custom").items()
                   if k.endswith("_one_cm") and k not in NOT_TRAVEL)

    @property
    def blocks_mined(self):
        return sum(self.category("mined").values())

    @property
    def diamonds(self):
        mined = self.category("mined")
        return sum(mined.get(k, 0) for k in DIAMOND_ORES)

    def favorite(self, category):
        """(id, count) with the highest count in the category, or None."""
        values = self.category(category)
        if not values:
            return None
        return max(values.items(), key=lambda kv: kv[1])


# /top <key>: (emoji name, title, value(stats), format(value))
TOP = {
    "time":     ("uptime",   "Время в игре",    lambda s: s.play_seconds, fmt_duration),
    "distance": ("distance", "Пройдено",        lambda s: s.distance_cm,  fmt_distance),
    "blocks":   ("mined",    "Добыто блоков",   lambda s: s.blocks_mined, fmt_int),
    "diamonds": ("diamond",  "Добыто алмазов",  lambda s: s.diamonds,     fmt_int),
    "mobs":     ("kills",    "Убито мобов",     lambda s: s.mob_kills,    fmt_int),
    "deaths":   ("death",    "Смерти",          lambda s: s.deaths,       fmt_int),
}
