"""Advancement messages from the server log: parsing, translation, noise filtering."""

import re
from dataclasses import dataclass

ADV_RE = re.compile(
    r"^([A-Za-z0-9_]{3,16}) has (made the advancement|reached the goal|completed the challenge) \[(.+)\]$"
)
FRAMES = {
    "made the advancement": "task",
    "reached the goal": "goal",
    "completed the challenge": "challenge",
}

# Tasks are mostly noise ("Stone Age", "Hot Stuff"): only these milestones are announced.
# Goals and challenges are rare and always announced.
NOTABLE_TASKS = {
    "story/mine_diamond",  # Алмазы!
    "story/shiny_gear",  # Осыпь меня алмазами
    "story/enter_the_nether",  # Огненные недра
    "story/enter_the_end",  # Конец?
    "nether/find_fortress",  # Чертоги страха
    "nether/obtain_ancient_debris",  # Осколки прошлого
    "nether/get_wither_skull",  # Бедный Йорик!
    "nether/summon_wither",  # Чудо-юдо
}

# id -> (fallback emoji, custom_emoji_id or None), like the in-game advancement icon
ADVANCEMENT_ICONS = {
    "story/mine_diamond": ("💎", "5773770578062216835"),
    "story/shiny_gear": ("🛡", "6278550001232647489"),
    "story/enter_the_nether": ("🔥", "5350762581973809979"),
    "story/enter_the_end": ("👁", "5775892283316508986"),
    "nether/find_fortress": ("🏰", "5316840028326875681"),
    "nether/obtain_ancient_debris": ("🪨", "5776127308221909787"),
    "nether/get_wither_skull": ("💀", "5778371776526421349"),
    "nether/summon_wither": ("⭐", "5429624675518916369"),
    "end/kill_dragon": ("🐉", "5431511738119897411"),
    "end/elytra": ("🪽", "5429425616669647986"),
}
# for everything without its own icon, like the frame shape in game
FRAME_ICONS = {
    "task": ("🏅", None),
    "goal": ("🎯", None),
    "challenge": ("🏆", "5422546251587527850"),
}


@dataclass(frozen=True)
class Advancement:
    id: str | None  # "story/mine_diamond"; None for unknown (modded) ones
    frame: str  # task / goal / challenge
    title: str  # russian if known
    description: str

    @property
    def notable(self):
        return self.frame != "task" or self.id in NOTABLE_TASKS

    @property
    def icon(self):
        """(fallback emoji, custom_emoji_id or None)"""
        icon = ADVANCEMENT_ICONS.get(self.id) if self.id else None
        return icon or FRAME_ICONS[self.frame]


class AdvancementParser:
    def __init__(self, en, ru):
        self._by_title = {}  # english title -> (id, ru title, ru description)
        for key, title in en.items():
            if not (key.startswith("advancements.") and key.endswith(".title")):
                continue
            base = key.removesuffix(".title")
            desc_key = base + ".description"
            self._by_title.setdefault(
                title,
                (
                    base.removeprefix("advancements.").replace(".", "/"),
                    ru.get(key, title),
                    ru.get(desc_key, en.get(desc_key, "")),
                ),
            )

    def parse(self, msg):
        """Returns (nick, Advancement) or None if msg is not an advancement message."""
        m = ADV_RE.match(msg)
        if not m:
            return None
        nick, verb, title = m.groups()
        adv_id, ru_title, desc = self._by_title.get(title, (None, title, ""))
        return nick, Advancement(adv_id, FRAMES[verb], ru_title, desc)
