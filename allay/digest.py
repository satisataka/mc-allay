"""Weekly digest: what happened on the server during the week.

Stats files only hold running totals, so at the start of every week the bot keeps a
snapshot of them in state/digest.json; the digest is the difference with the current
files. Deaths and advancements are recorded there too as the bot sees them in the log.
"""

import html
import json
import threading
import time
from collections import Counter
from datetime import datetime, timedelta

from .lang import mc_name
from .stats import TOP, PlayerStats
from .util import e, emoji, fmt_duration, fmt_size, load_json, log
from .workers import beat

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
# death.attack.<kind>...: ordinary kills never make the "funniest death"
BORING_DEATHS = {"mob", "player", "arrow"}
MAX_ADVANCEMENTS = 10
ACTIVE_SECONDS = 60  # played at least a minute this week


def week_label(start, end):
    """'6–12 октября', '29 сентября – 5 октября', or '8 октября' for a single day."""
    if start.date() == end.date():
        return f"{end.day} {MONTHS[end.month - 1]}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{start.day} {MONTHS[start.month - 1]} – {end.day} {MONTHS[end.month - 1]}"


def next_due(after, weekday, hour, minute, tz):
    """Epoch of the first weekday (1-7, Mon-Sun) hour:minute in tz strictly after `after`."""
    dt = datetime.fromtimestamp(after, tz)
    target = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=(weekday - 1 - dt.weekday()) % 7)
    if target.timestamp() <= after:
        target += timedelta(days=7)
    return target.timestamp()


def days_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def is_boring(death):
    parts = death["key"].split(".")
    return len(parts) > 2 and parts[1] == "attack" and parts[2] in BORING_DEATHS


class Digest:
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.server = bot.server
        self.path = self.cfg.players_file.parent / "digest.json"
        self.lock = threading.Lock()
        self.state = load_json(self.path, None)
        if not isinstance(self.state, dict) or "started_at" not in self.state:
            self.state = self.new_week(time.time(), prev_play=None)
            self.save()
            log.info("digest: new week started")

    # --- state ---

    def new_week(self, now, prev_play):
        stats = {}
        for _, uuid in self.bot.visible_players():
            raw = self.server.player_stats(uuid)
            if raw is not None:
                stats[uuid] = raw
        return {
            "started_at": now,
            "stats": stats,
            "world_size": self.server.world_size(),
            "day": self.server.time_query("day"),
            "prev_play": prev_play,  # previous week's total play time, for the comparison
            "deaths": [],
            "advancements": [],
        }

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False))
        tmp.replace(self.path)

    def record_death(self, nick, key, text):
        with self.lock:
            self.state["deaths"].append({"at": time.time(), "nick": nick, "key": key, "text": text})
            self.save()

    def record_advancement(self, nick, adv, first):
        with self.lock:
            self.state["advancements"].append(
                {"at": time.time(), "nick": nick, "title": adv.title, "icon": list(adv.icon), "first": first})
            self.save()

    # --- schedule ---

    def due(self):
        hour, minute = self.cfg.digest_time
        return next_due(self.state["started_at"], self.cfg.digest_weekday, hour, minute, self.cfg.tz)

    def run(self):
        while True:
            beat("digest")
            if time.time() >= self.due():  # also catches up after the bot was down
                self.publish()
            time.sleep(60)

    def publish(self):
        now = time.time()
        with self.lock:
            text, total_play = self.render(now, final=True)
            if not self.bot.publish(text):
                return  # Telegram unreachable: try again in a minute, the week isn't rolled
            self.state = self.new_week(now, prev_play=total_play)
            self.save()
        log.info("digest published, new week started")

    # --- rendering ---

    def preview(self):
        """Digest of the week so far, for /week."""
        with self.lock:
            return self.render(time.time(), final=False)[0]

    def render(self, now, final):
        """(html, total play seconds of the week)."""
        st = self.state
        tz = self.cfg.tz
        label = week_label(datetime.fromtimestamp(st["started_at"], tz), datetime.fromtimestamp(now, tz))
        title = (f"{e('digest')} <b>Итоги недели</b> · {label}" if final
                 else f"{e('digest')} <b>Промежуточные итоги недели</b> · {label}")
        ph = self.bot.player_html

        visible = self.bot.visible_players()
        players = []  # (nick, now, week start, joined for the first time this week)
        for nick, uuid in visible:
            raw = self.server.player_stats(uuid)
            if raw is not None:
                players.append((nick, PlayerStats(raw), PlayerStats(st["stats"].get(uuid)), uuid not in st["stats"]))

        def delta(value):
            """[(nick, change this week)], biggest first."""
            rows = [(nick, value(cur) - value(old)) for nick, cur, old, _ in players]
            return sorted(rows, key=lambda r: r[1], reverse=True)

        play = delta(lambda s: s.play_seconds)
        total_play = sum(v for _, v in play)
        active = [(n, v) for n, v in play if v >= ACTIVE_SECONDS]
        if not active:
            return f"{title}\n\nТихая неделя, сервер скучает 🥲", total_play

        compare = ""
        prev = st.get("prev_play")
        if prev:
            pct = round((total_play - prev) / prev * 100)
            compare = f" ({'+' if pct >= 0 else ''}{pct}% к прошлой неделе)"
        lines = [
            title,
            "",
            f"{e('online')} Играли {len(active)} из {len(visible)} · всего {fmt_duration(total_play)}{compare}",
            f"{e('top1')} Игрок недели: {ph(active[0][0])} · {fmt_duration(active[0][1])}",
        ]
        newcomers = [nick for nick, _, _, new in players if new]
        if newcomers:
            lines.append(f"{e('newcomer')} Новенькие: " + ", ".join(ph(n) for n in newcomers))

        # records: top 3 by play time, a leader per other category
        icon, title_, _, _, fmt = TOP["time"]
        lines += ["", f"{e('top')} <b>Рекорды недели</b>", f"{e(icon)} {title_}"]
        lines += [f"{e(f'top{i + 1}')} {ph(n)} · {fmt_duration(v)}" for i, (n, v) in enumerate(active[:3])]
        for key in ("distance", "blocks", "diamonds", "mobs"):
            icon, _, label_, value, fmt = TOP[key]
            nick, best = delta(value)[0]
            if best > 0:
                lines.append(f"{e(icon)} {label_}: {ph(nick)} · {fmt(best)}")

        lines += self.render_deaths(players, delta)
        lines += self.render_advancements(ph)

        world = []
        growth = self.server.world_size() - (st.get("world_size") or 0)
        if st.get("world_size") and growth > 0:
            world.append(f"мир подрос на {fmt_size(growth)}")
        day = self.server.time_query("day")
        if day is not None and st.get("day") is not None and day > st["day"]:
            passed = day - st["day"]
            world.append(f"в игре прошло {passed} {days_word(passed)}")
        if world:
            text = ", ".join(world)
            lines += ["", f"{e('world')} {text[0].upper()}{text[1:]}"]
        return "\n".join(lines), total_play

    def render_deaths(self, players, delta):
        deaths = [(n, v) for n, v in delta(lambda s: s.deaths) if v > 0]
        if not deaths:
            return []
        lines = ["", f"{e('death')} Смертей за неделю: {sum(v for _, v in deaths)}"
                     f" · чаще всех: {self.bot.player_html(deaths[0][0])} ({deaths[0][1]})"]

        killers = Counter()
        for _, cur, old, _ in players:
            before = old.category("killed_by")
            for mob, n in cur.category("killed_by").items():
                killers[mob] += n - before.get(mob, 0)
        killer, n = max(killers.items(), key=lambda kv: kv[1], default=(None, 0))
        if n > 0:
            lines.append(f"{e('killer')} Главный убийца недели: {html.escape(mc_name(self.bot.ru, killer))} ({n})")

        funny = [d for d in self.state["deaths"] if not is_boring(d)]
        if funny:
            counts = Counter(d["key"] for d in funny)
            # the rarest kind of death; among equals the latest
            best = min(reversed(funny), key=lambda d: counts[d["key"]])
            lines.append(f"{e('funny_death')} Самая нелепая смерть: {best['text']}")
        return lines

    def render_advancements(self, ph):
        advs = self.state["advancements"]
        if not advs:
            return []
        lines = ["", f"{e('adv_many')} <b>Достижения недели</b>"]
        for a in advs[:MAX_ADVANCEMENTS]:
            first = f" · {e('adv_first')} первым" if a["first"] else ""
            lines.append(f"{emoji(*a['icon'])} {ph(a['nick'])} — «{html.escape(a['title'])}»{first}")
        if len(advs) > MAX_ADVANCEMENTS:
            lines.append(f"<i>…и ещё {len(advs) - MAX_ADVANCEMENTS}</i>")
        return lines
