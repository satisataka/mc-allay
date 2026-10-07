"""Allay: reacts to server events and answers chat commands."""

import html
import json
import re
import time

from .lang import mc_name
from .stats import TOP, PlayerStats
from .util import e, emoji, fmt_distance, fmt_duration, fmt_int, fmt_seen, fmt_size, load_json, log
from .workers import Batcher

NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
TG_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
LOG_RE = re.compile(r"^\[[^\]]+\] \[Server thread/INFO\]: (.*)$")

# a fresh player often gets several advancements in a row: they go out as one message
ADVANCEMENT_BATCH_SECONDS = 45
ADVANCEMENT_VERBS = {
    "task": "получил достижение",
    "goal": "достиг цели",
    "challenge": "завершил испытание",
}

DIFFICULTY = {"Peaceful": "мирная", "Easy": "лёгкая", "Normal": "нормальная", "Hard": "сложная"}


def world_clock(daytime):
    """Ticks since 6:00 -> ('14:30', emoji name, phase)."""
    daytime %= 24000
    clock = f"{(daytime // 1000 + 6) % 24:02d}:{daytime % 1000 * 60 // 1000:02d}"
    if daytime < 12000:
        return clock, "day", "день"
    if daytime < 13000:
        return clock, "dusk", "закат"
    if daytime < 23000:
        return clock, "night", "ночь"
    return clock, "dusk", "рассвет"


class Allay:
    def __init__(self, cfg, tg, server, deaths, advancements, ru):
        self.cfg = cfg
        self.tg = tg
        self.server = server
        self.deaths = deaths
        self.advancements = advancements
        self.ru = ru  # russian lang file, for block/mob names in /stats
        self.adv_batch = Batcher(ADVANCEMENT_BATCH_SECONDS, self.announce_advancements)
        self.adv_seen = set()  # advancement ids announced since start (files may lag behind)
        self.sessions = {}  # lowercased nick -> join timestamp
        self.username = ""
        # name -> (description, handler(args, msg), admin_only)
        self.commands = {
            "status": ("Состояние сервера", self.cmd_status, False),
            "players": ("Игроки и последний вход", self.cmd_players, False),
            "world": ("Информация о мире", self.cmd_world, False),
            "top": ("Рейтинги игроков", self.cmd_top, False),
            "stats": ("Статистика игрока: [ник]", self.cmd_stats, False),
            "wl_add": ("Добавить игрока: ник [@telegram]", self.cmd_wl_add, True),
            "wl_remove": ("Удалить игрока из whitelist", self.cmd_wl_remove, True),
        }

    # --- helpers ---

    def is_hidden(self, nick):
        return nick.lower() in self.cfg.hidden

    def load_players(self):
        """players.json: nick -> {"telegram": "..."}, maintained by /wl_add and /wl_remove."""
        return load_json(self.cfg.players_file, {})

    def save_players(self, players):
        tmp = self.cfg.players_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(players, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(self.cfg.players_file)  # atomic: readers never see a half-written file

    def telegram_of(self, nick):
        return self.load_players().get(nick, {}).get("telegram")

    def is_admin(self, user_id):
        return self.cfg.admin_id is not None and user_id == self.cfg.admin_id

    def player_html(self, nick):
        """Bold nick; link opens the Telegram profile page (not the chat), and doesn't ping."""
        name = f"<b>{html.escape(nick)}</b>"
        tg_name = self.telegram_of(nick)
        return f'<a href="https://t.me/{html.escape(tg_name)}?profile">{name}</a>' if tg_name else name

    def online(self):
        """Like Server.online_players, but without hidden players in the names list."""
        online = self.server.online_players()
        if online is None:
            return None
        count, max_players, names = online
        return count, max_players, [n for n in names if not self.is_hidden(n)]

    def send(self, text, silent=False):
        return self.tg.send(self.cfg.chat_id, text, silent=silent)

    def visible_players(self):
        """[(nick, uuid)] from the whitelist, hidden players excluded."""
        return [(x["name"], x.get("uuid", "")) for x in self.server.whitelist()
                if x.get("name") and not self.is_hidden(x["name"])]

    def played_stats(self):
        """[(nick, uuid, PlayerStats)] for visible players who have played at least once."""
        result = []
        for nick, uuid in self.visible_players():
            raw = self.server.player_stats(uuid)
            if raw is not None:
                result.append((nick, uuid, PlayerStats(raw)))
        return result

    def find_nick(self, query):
        """Whitelisted nick, case-insensitive; None if absent or hidden."""
        for nick, _ in self.visible_players():
            if nick.lower() == query.lower():
                return nick
        return None

    def nick_by_telegram(self, username):
        for nick, info in self.load_players().items():
            if str(info.get("telegram", "")).lower() == username.lower():
                return self.find_nick(nick)
        return None

    # --- events ---

    def announce_new_player(self, nick):
        if self.is_hidden(nick):
            log.info(f"whitelist: {nick} is hidden, no announcement")
            return

        who = f"<b>{html.escape(nick)}</b>"
        tg_name = self.telegram_of(nick)
        if tg_name:
            who += f" (@{html.escape(tg_name)})"

        text = (
            f"{e('welcome')} <b>В нашем полку прибыло!</b>\n\n"
            f"Встречаем {who} — добро пожаловать в <b>{html.escape(self.cfg.server_name)}</b>!\n\n"
            f"{e('whitelist')} Ты в whitelist, сервер ждёт\n"
            f"{e('lock')} При первом входе: <code>/register пароль пароль</code>\n\n"
            f"Ребята, покажите новенькому, где тут что {e('pickaxe')}"
        )
        if self.cfg.channel_id and self.post_to_channel(text):
            log.info(f"announced new player {nick} via channel")
            return
        self.send(text)
        log.info(f"announced new player {nick}")

    def post_to_channel(self, text):
        """Publishes text in the channel; the chat gets it as the linked channel's auto-forward.

        Bots can't use custom emoji in channels directly, but a forward keeps them: the text
        is drafted in the admin's private chat (or the server chat), forwarded and deleted.
        """
        draft_chat = self.cfg.admin_id or self.cfg.chat_id
        draft = self.tg.send(draft_chat, text, silent=True)
        if not draft:
            return False
        posted = self.tg.call("forwardMessage", chat_id=self.cfg.channel_id,
                              from_chat_id=draft_chat, message_id=draft["message_id"])
        self.tg.call("deleteMessage", chat_id=draft_chat, message_id=draft["message_id"])
        return posted is not None

    def on_join(self, nick):
        if self.is_hidden(nick):
            return
        self.sessions[nick.lower()] = time.time()

        uuid = self.server.uuid_of(nick)
        # player data file appears on first save, so at join time it's missing only for newcomers
        first_time = uuid is not None and self.server.last_seen(uuid) is None
        name = self.player_html(nick)

        if first_time:
            self.send(f"{e('first_join')} {name} впервые в нашем мире! Встречайте")
        else:
            online = self.online()
            count = f" · {e('online')} {online[0]}/{online[1]}" if online else ""
            self.send(f"{e('join')} {name} зашёл на сервер{count}", silent=True)
        log.info(f"join: {nick} (first={first_time})")

    def on_leave(self, nick):
        if self.is_hidden(nick):
            return
        joined_at = self.sessions.pop(nick.lower(), None)

        parts = [f"{e('leave')} {self.player_html(nick)} вышел с сервера"]
        if joined_at:  # unknown if the player joined before the bot started
            played = time.time() - joined_at
            parts.append("в игре " + ("меньше минуты" if played < 60 else fmt_duration(played)))
        online = self.online()
        if online:
            parts.append(f"{e('online')} {online[0]}/{online[1]}")

        self.send(" · ".join(parts), silent=True)
        log.info(f"leave: {nick}")

    def on_death(self, msg):
        result = self.deaths.translate(msg, self.player_html)
        if not result:
            return
        victim, text = result
        # only real players: rejects named mobs and anything that isn't in the whitelist
        if not NICK_RE.match(victim) or self.server.uuid_of(victim) is None or self.is_hidden(victim):
            return
        self.send(f"{e('death')} {text}", silent=True)
        log.info(f"death: {msg}")

    def on_advancement(self, msg):
        """Returns False if msg is not an advancement message."""
        parsed = self.advancements.parse(msg)
        if not parsed:
            return False
        nick, adv = parsed
        if not adv.notable or self.is_hidden(nick):
            return True
        uuid = self.server.uuid_of(nick)
        if uuid is None:
            return True  # not a whitelisted player

        first = False
        if adv.id is not None and adv.id not in self.adv_seen:
            # hidden players (admin testing things) don't take "first" away from others
            hidden = {x.get("uuid") for x in self.server.whitelist() if self.is_hidden(x.get("name", ""))}
            first = not (self.server.advancement_done_by(adv.id) - {uuid} - hidden)
            self.adv_seen.add(adv.id)

        self.adv_batch.add(nick, (adv, first))
        log.info(f"advancement: {nick} {adv.frame} {adv.id} (first={first})")
        return True

    def announce_advancements(self, nick, items):
        name = self.player_html(nick)
        loud = any(first for _, first in items)

        def title(adv, first):
            text = f"«{html.escape(adv.title)}»"
            return text + f" · {e('adv_first')} первым на сервере!" if first else text

        if len(items) == 1:
            adv, first = items[0]
            text = f"{emoji(*adv.icon)} {name} {ADVANCEMENT_VERBS[adv.frame]} {title(adv, first)}"
            if adv.description:
                text += f"\n<i>{html.escape(adv.description)}</i>"
        else:
            lines = [f"{e('adv_many')} {name} получил достижения:"]
            lines += [f"{emoji(*adv.icon)} {title(adv, first)}" for adv, first in items]
            text = "\n".join(lines)
        # only "first on the server" is worth a notification
        self.send(text, silent=not loud)

    def handle_log_line(self, line):
        m = LOG_RE.match(line)
        if not m:
            return
        raw = m.group(1)
        is_system = raw.startswith("System chat: ")
        msg = raw.removeprefix("System chat: ")

        if msg.endswith(" joined the game"):
            nick = msg[: -len(" joined the game")]
            if NICK_RE.match(nick):  # rejects chat lines like "<Ruslan> x joined the game"
                self.on_join(nick)
        elif msg.endswith(" left the game"):
            nick = msg[: -len(" left the game")]
            if NICK_RE.match(nick):
                self.on_leave(nick)
        elif msg.startswith("Done ("):
            self.server.started_at = time.time()
        elif not self.on_advancement(msg) and is_system:
            self.on_death(msg)

    # --- commands ---

    def cmd_status(self, args, msg):
        server_name = html.escape(self.cfg.server_name)
        online = self.online()
        if online is None:
            return f"{e('offline')} <b>{server_name}</b> сейчас недоступен"

        count, max_players, names = online
        who = (f"Онлайн {count}/{max_players}: " + ", ".join(html.escape(n) for n in names)
               if names else "Онлайн пока никого")
        lines = [
            f"{e('world')} <b>{server_name}</b> работает",
            f"{e('online')} {who}",
        ]

        ts = self.server.tick_stats()
        if ts:
            avg, p95 = ts
            ref = p95 if p95 is not None else avg
            grade = "отлично" if ref < 25 else "нормально" if ref < 40 else "тормозит"
            p95_txt = f" (p95 {p95:.1f})" if p95 is not None else ""
            lines.append(f"{e('speed')} MSPT: {avg:.1f} мс{p95_txt} — {grade}")

        if self.server.started_at:
            lines.append(f"{e('uptime')} Аптайм: {fmt_duration(time.time() - self.server.started_at)}")

        lines.append(f"{e('disk')} Диск: {fmt_size(self.server.disk_free())} свободно"
                     f" · мир {fmt_size(self.server.world_size())}")

        age = self.server.last_backup_age()
        lines.append(f"{e('backup')} Бэкап: " + (f"{fmt_duration(age)} назад" if age is not None else "нет"))
        return "\n".join(lines)

    def cmd_players(self, args, msg):
        online = self.online()
        online_names = {n.lower() for n in online[2]} if online else set()

        rows = []
        for entry in self.server.whitelist():
            name = entry.get("name", "?")
            if self.is_hidden(name):
                continue
            seen = self.server.last_seen(entry.get("uuid", ""))
            rows.append((name.lower() in online_names, seen or 0.0, name, seen))
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)  # online first, then most recent

        lines = [f"{e('online')} <b>Игроки ({len(rows)})</b>", ""]
        for is_online, _, name, seen in rows:
            n = self.player_html(name)
            if is_online:
                lines.append(f"{e('p_online')} {n} · в игре")
            elif seen:
                lines.append(f"{e('p_seen')} {n} · {fmt_seen(seen, self.cfg.tz)}")
            else:
                lines.append(f"{e('p_never')} {n} · ещё не заходил")

        if online is None:
            lines += ["", "<i>Сервер недоступен, онлайн неизвестен</i>"]
        return "\n".join(lines)

    def cmd_world(self, args, msg):
        lines = [f"{e('world')} <b>{html.escape(self.cfg.server_name)}</b>", ""]

        day, daytime = self.server.time_query("day"), self.server.time_query("daytime")
        if day is not None and daytime is not None:
            clock, icon, phase = world_clock(daytime)
            lines.append(f"{e(icon)} День {fmt_int(day + 1)} · {clock}, {phase}")
        difficulty = self.server.difficulty()
        if difficulty:
            lines.append(f"{e('difficulty')} Сложность: {DIFFICULTY.get(difficulty, difficulty)}")
        version = self.server.version()
        if version:
            lines.append(f"{e('version')} Версия: {html.escape(version)}")
        if day is None:
            lines.append("<i>Сервер недоступен, время и сложность неизвестны</i>")

        players = self.played_stats()
        lines += ["", f"{e('online')} Игроков: {len(self.visible_players())} · заходили: {len(players)}"]
        if players:
            def total(value):
                return sum(value(s) for _, _, s in players)

            advancements = sum(self.server.advancement_count(uuid) for _, uuid, _ in players)
            most_active = max(players, key=lambda p: p[2].play_seconds)
            lines += [
                f"{e('uptime')} Наиграно всего: {fmt_duration(total(lambda s: s.play_seconds))}",
                f"{e('distance')} Пройдено вместе: {fmt_distance(total(lambda s: s.distance_cm))}",
                f"{e('mined')} Добыто блоков: {fmt_int(total(lambda s: s.blocks_mined))}"
                f" · {e('diamond')} алмазов: {fmt_int(total(lambda s: s.diamonds))}",
                f"{e('kills')} Убито мобов: {fmt_int(total(lambda s: s.mob_kills))}",
                f"{e('death')} Смертей: {fmt_int(total(lambda s: s.deaths))}",
                f"{e('advancements')} Достижений получено: {fmt_int(advancements)}",
                "",
                f"{e('top1')} Самый активный: {self.player_html(most_active[0])}"
                f" · {fmt_duration(most_active[2].play_seconds)}",
            ]
        return "\n".join(lines)

    def cmd_top(self, args, msg):
        usage = "Категории: " + " · ".join(f"<code>/top {k}</code>" for k in TOP)
        if len(args) > 1 or (args and args[0].lower() not in TOP):
            return usage
        players = self.played_stats()
        if not players:
            return "Пока никто не играл"

        def place(i):
            return e(f"top{i + 1}") if i < 3 else f"{i + 1}."

        if args:  # one category, everyone
            icon, title, value, fmt = TOP[args[0].lower()]
            rows = sorted(players, key=lambda p: value(p[2]), reverse=True)
            lines = [f"{e(icon)} <b>{title}</b>", ""]
            lines += [f"{place(i)} {self.player_html(nick)} · {fmt(value(s))}"
                      for i, (nick, _, s) in enumerate(rows)]
            return "\n".join(lines)

        # overview: top 3 per category
        lines = [f"{e('top')} <b>Топ игроков</b>"]
        for icon, title, value, fmt in TOP.values():
            rows = sorted(((nick, value(s)) for nick, _, s in players if value(s) > 0),
                          key=lambda r: r[1], reverse=True)[:3]
            if rows:
                lines += ["", f"{e(icon)} <b>{title}</b>"]
                lines.append(" · ".join(f"{place(i)} {self.player_html(nick)} {fmt(v)}"
                                        for i, (nick, v) in enumerate(rows)))
        lines += ["", f"<i>Весь рейтинг: {usage.removeprefix('Категории: ')}</i>"]
        return "\n".join(lines)

    def cmd_stats(self, args, msg):
        if len(args) > 1:
            return "Использование: <code>/stats [ник]</code>"
        if args:
            nick = self.find_nick(args[0])
            if nick is None:
                return f"Игрока <b>{html.escape(args[0])}</b> нет в whitelist"
        else:
            username = msg.get("from", {}).get("username")
            nick = self.nick_by_telegram(username) if username else None
            if nick is None:
                return "Не знаю твой ник в игре 🤔 Укажи его: <code>/stats ник</code>"

        name = self.player_html(nick)
        uuid = self.server.uuid_of(nick)
        raw = self.server.player_stats(uuid)
        if raw is None:
            return f"{e('p_never')} {name} ещё не заходил на сервер"
        s = PlayerStats(raw)

        online = self.online()
        if online and nick.lower() in {n.lower() for n in online[2]}:
            presence = f"{e('p_online')} сейчас в игре"
        else:
            seen = self.server.last_seen(uuid)
            presence = f"был {fmt_seen(seen, self.cfg.tz)}" if seen else ""

        lines = [
            f"{e('stats')} <b>Статистика</b> {name}",
            "",
            f"{e('uptime')} В игре: {fmt_duration(s.play_seconds)}" + (f" · {presence}" if presence else ""),
            f"{e('distance')} Пройдено: {fmt_distance(s.distance_cm)}",
            f"{e('mined')} Добыто блоков: {fmt_int(s.blocks_mined)} · {e('diamond')} алмазов: {fmt_int(s.diamonds)}",
            f"{e('kills')} Убито мобов: {fmt_int(s.mob_kills)}"
            + (f" · игроков: {fmt_int(s.player_kills)}" if s.player_kills else ""),
            f"{e('death')} Смертей: {fmt_int(s.deaths)}"
            + (f" · без смертей уже {fmt_duration(s.since_death_seconds)}"
               if s.deaths and s.since_death_seconds >= 60 else ""),
            f"{e('advancements')} Достижений: {fmt_int(self.server.advancement_count(uuid))}",
        ]

        favorites = [
            ("favorite", "Любимый блок", "mined"),
            ("kills", "Главная жертва", "killed"),
            ("death", "Главный враг", "killed_by"),
        ]
        extra = []
        for icon, title, category in favorites:
            fav = s.favorite(category)
            if fav:
                extra.append(f"{e(icon)} {title}: {html.escape(mc_name(self.ru, fav[0]))} · {fmt_int(fav[1])}")
        if extra:
            lines += ["", *extra]
        return "\n".join(lines)

    def cmd_wl_add(self, args, msg):
        if not args or len(args) > 2 or not NICK_RE.match(args[0]):
            return "Использование: <code>/wl_add ник [@telegram]</code>"
        nick = args[0]
        tg_name = args[1].removeprefix("@") if len(args) > 1 else None
        if tg_name is not None and not TG_USERNAME_RE.match(tg_name):
            return f"Некорректный telegram username: <code>{html.escape(args[1])}</code>"

        # telegram mapping first: the whitelist watcher reads it for the announcement
        if tg_name:
            players = self.load_players()
            players[nick] = {"telegram": tg_name}
            self.save_players(players)

        uuid, already, reloaded = self.server.whitelist_add(nick)
        log.info(f"wl_add: {nick} -> {uuid} (telegram={tg_name}, already={already}, reloaded={reloaded})")

        name = f"<b>{html.escape(nick)}</b>"
        lines = [f"{e('whitelist')} {name} уже был в whitelist, запись обновлена" if already
                 else f"{e('whitelist')} {name} добавлен в whitelist"]
        lines.append(f"UUID: <code>{uuid}</code>")
        if tg_name:
            lines.append(f"Telegram: @{html.escape(tg_name)}")
        if not reloaded:
            lines.append("<i>Сервер недоступен, whitelist применится при запуске</i>")
        return "\n".join(lines)

    def cmd_wl_remove(self, args, msg):
        if len(args) != 1 or not NICK_RE.match(args[0]):
            return "Использование: <code>/wl_remove ник</code>"
        nick = args[0]

        removed, reloaded = self.server.whitelist_remove(nick)
        players = self.load_players()
        kept = {k: v for k, v in players.items() if k.lower() != nick.lower()}
        if len(kept) != len(players):
            self.save_players(kept)
        log.info(f"wl_remove: {nick} (removed={removed}, reloaded={reloaded})")

        if removed is None:
            return f"<b>{html.escape(nick)}</b> нет в whitelist"
        text = f"{e('leave')} <b>{html.escape(removed)}</b> удалён из whitelist"
        if not reloaded:
            text += "\n<i>Сервер недоступен, whitelist применится при запуске</i>"
        return text

    def handle_message(self, msg):
        user_id = msg.get("from", {}).get("id")
        is_admin = self.is_admin(user_id)
        # commands work in our server chat, plus the admin's private chat with the bot
        if msg["chat"]["id"] != self.cfg.chat_id and not (msg["chat"]["type"] == "private" and is_admin):
            return

        text = msg.get("text", "")
        if not text.startswith("/"):
            return
        head, *args = text.split()
        cmd, _, target = head[1:].partition("@")
        if target and target.lower() != self.username:
            return  # command addressed to another bot
        entry = self.commands.get(cmd.lower())
        if not entry:
            return
        _, handler, admin_only = entry

        if admin_only and not is_admin:
            # user id in the log helps to fill TG_ADMIN_ID
            log.info(f"/{cmd} rejected: user id {user_id} is not admin")
            reply = "Эта команда доступна только админу"
        else:
            try:
                reply = handler(args, msg)
            except Exception as ex:
                log.info(f"/{cmd} failed: {ex!r}")
                reply = "Не получилось выполнить команду 😕"
        self.tg.send(msg["chat"]["id"], reply, reply_to=msg["message_id"])

    def _set_commands(self):
        def menu(admin):
            return [{"command": name, "description": desc}
                    for name, (desc, _, admin_only) in self.commands.items() if admin or not admin_only]

        self.tg.call("setMyCommands", commands=menu(admin=False))
        if self.cfg.admin_id is None:
            log.info("TG_ADMIN_ID is not set, admin commands are disabled")
            return
        # the admin sees the full menu in the server chat and in the private chat
        for scope in ({"type": "chat_member", "chat_id": self.cfg.chat_id, "user_id": self.cfg.admin_id},
                      {"type": "chat", "chat_id": self.cfg.admin_id}):
            self.tg.call("setMyCommands", commands=menu(admin=True), scope=scope)

    def poll_commands(self):
        me = self.tg.call("getMe")
        while me is None:
            time.sleep(10)
            me = self.tg.call("getMe")
        self.username = me["username"].lower()

        self._set_commands()

        # drop commands sent while the bot was down
        pending = self.tg.call("getUpdates", offset=-1, timeout=0) or []
        offset = pending[-1]["update_id"] + 1 if pending else None
        log.info(f"bot @{self.username} ready")

        while True:
            params = {"timeout": 50, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            updates = self.tg.call("getUpdates", http_timeout=60, **params)
            if updates is None:
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                if "message" in u:
                    self.handle_message(u["message"])
