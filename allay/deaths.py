"""Translates vanilla death messages from the server log into Russian."""

import html
import re

from .util import load_json, log

ARG_RE = re.compile(r"%(?:(\d+)\$)?s")


def _template_regex(template):
    """'%1$s was slain by %2$s' -> regex with named groups a1, a2."""
    out, auto = "", 0
    for part in re.split(r"(%(?:\d+\$)?s)", template):
        m = ARG_RE.fullmatch(part)
        if m:
            auto += 1
            n = int(m.group(1)) if m.group(1) else auto
            out += f"(?P<a{n}>.+?)"
        else:
            out += re.escape(part)
    return re.compile(f"^{out}$")


class DeathTranslator:
    def __init__(self, lang_dir):
        self.patterns, self.names = self._load(lang_dir)

    @staticmethod
    def _load(lang_dir):
        """Returns ([(regex, ru_template)], en->ru name map) or ([], {}) if files are missing."""
        en = load_json(lang_dir / "en_us.json", {})
        ru = load_json(lang_dir / "ru_ru.json", {})
        if not en or not ru:
            log.info(f"no language files in {lang_dir}, death messages stay in english")
            return [], {}

        patterns = []
        for key, en_tpl in en.items():
            if not key.startswith("death.") or key not in ru:
                continue
            try:
                patterns.append((_template_regex(en_tpl), ru[key], len(ARG_RE.sub("", en_tpl))))
            except re.error:
                continue  # duplicate arg in one template, skip
        # most specific first: "slain by X using Y" must win over "slain by X"
        patterns.sort(key=lambda p: p[2], reverse=True)

        names = {
            en[k]: ru[k]
            for k in en
            if k in ru and k.startswith(("entity.minecraft.", "item.minecraft.", "block.minecraft."))
        }
        log.info(f"loaded {len(patterns)} death messages, {len(names)} names")
        return [(rx, tpl) for rx, tpl, _ in patterns], names

    def _ru_name(self, arg):
        """Translate mob/item names; items come as '[Iron Sword]', player names stay as is."""
        if arg.startswith("[") and arg.endswith("]"):
            inner = arg[1:-1]
            return f"«{self.names.get(inner, inner)}»"
        return self.names.get(arg, arg)

    def translate(self, msg, render_victim):
        """Returns (victim, russian_text_html) or None if msg is not a death message.

        render_victim(nick) -> html for the first argument (the player who died).
        """
        for rx, ru_tpl in self.patterns:
            m = rx.match(msg)
            if not m:
                continue
            args = m.groupdict()
            counter = 0

            def sub(match):
                nonlocal counter
                counter += 1
                n = int(match.group(1)) if match.group(1) else counter
                value = args.get(f"a{n}", "")
                if n == 1:
                    return render_victim(value)
                return html.escape(self._ru_name(value))

            return args.get("a1", ""), ARG_RE.sub(sub, ru_tpl)
        return None
