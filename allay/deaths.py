"""Translates vanilla death messages from the server log into Russian."""

import html
import re

from .util import log

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
    def __init__(self, en, ru):
        self.patterns, self.names = self._load(en, ru)

    @staticmethod
    def _load(en, ru):
        """Returns ([(regex, ru_template, key)], en->ru name map)."""
        patterns = []
        for key, en_tpl in en.items():
            if not key.startswith("death.") or key not in ru:
                continue
            try:
                patterns.append((_template_regex(en_tpl), ru[key], key, len(ARG_RE.sub("", en_tpl))))
            except re.error:
                continue  # duplicate arg in one template, skip
        # most specific first: "slain by X using Y" must win over "slain by X"
        patterns.sort(key=lambda p: p[3], reverse=True)

        names = {
            en[k]: ru[k]
            for k in en
            if k in ru and k.startswith(("entity.minecraft.", "item.minecraft.", "block.minecraft."))
        }
        log.info(f"loaded {len(patterns)} death messages, {len(names)} names")
        return [(rx, tpl, key) for rx, tpl, key, _ in patterns], names

    def _ru_name(self, arg):
        """Translate mob/item names; items come as '[Iron Sword]', player names stay as is."""
        if arg.startswith("[") and arg.endswith("]"):
            inner = arg[1:-1]
            return f"«{self.names.get(inner, inner)}»"
        return self.names.get(arg, arg)

    def translate(self, msg, render_victim, render_player=None):
        """Returns (victim, russian_text_html, lang_key) or None if msg is not a death message.

        render_victim(nick) -> html for the first argument (the player who died).
        render_player(arg) -> html if another argument is a player (a killer), else None.
        """
        for rx, ru_tpl, key in self.patterns:
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
                player = render_player(value) if render_player else None
                return player or html.escape(self._ru_name(value))

            return args.get("a1", ""), ARG_RE.sub(sub, ru_tpl), key
        return None
