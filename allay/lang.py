"""Minecraft language files (lang/en_us.json, lang/ru_ru.json)."""

from .util import load_json, log


def mc_name(ru, mc_id):
    """'minecraft:creeper' -> 'Крипер': block, item or entity name, or the bare id if unknown."""
    name = mc_id.removeprefix("minecraft:")
    for kind in ("block", "item", "entity"):
        title = ru.get(f"{kind}.minecraft.{name}")
        if title:
            return title
    return name


def load_lang(lang_dir):
    """Returns (en, ru) dicts, or ({}, {}) if the files are missing: messages then stay in english."""
    en = load_json(lang_dir / "en_us.json", {})
    ru = load_json(lang_dir / "ru_ru.json", {})
    if not en or not ru:
        log.info(f"no language files in {lang_dir}, messages stay in english")
        return {}, {}
    return en, ru
