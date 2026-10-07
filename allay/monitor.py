"""Admin alerts in the private chat: server down, crashes, lag, stale backups, low disk."""

import html
import time

from .util import e, fmt_duration, fmt_size, log
from .workers import beat

CHECK_INTERVAL = 60
# mc-backup pauses while nobody plays: a backup is overdue only if someone has been playing a while
PLAYING_FOR_BACKUP = 30 * 60


class Problem:
    """Debounced state of one check.

    Raised after `raise_after` seconds of bad readings, cleared after `clear_after`
    seconds of good ones, reminded every `repeat` seconds while active.
    """

    def __init__(self, clear_after=0, repeat=None):
        self.clear_after = clear_after
        self.repeat = repeat
        self.active = False
        self.since = None       # first bad reading of the current streak
        self.good_since = None  # first good reading while active
        self.alerted_at = None
        self.duration = None    # how long the last cleared problem lasted

    def update(self, bad, raise_after, now):
        """bad: True / False / None (unknown, changes nothing). Returns 'raised', 'repeat', 'cleared' or None."""
        if bad is None:
            return None
        if bad:
            self.good_since = None
            if self.since is None:
                self.since = now
            if not self.active:
                if now - self.since >= raise_after:
                    self.active, self.alerted_at = True, now
                    return "raised"
            elif self.repeat and now - self.alerted_at >= self.repeat:
                self.alerted_at = now
                return "repeat"
            return None
        if not self.active:
            self.since = None
            return None
        if self.good_since is None:
            self.good_since = now
        if now - self.good_since >= self.clear_after:
            self.active = False
            self.duration = self.good_since - self.since
            self.since = self.good_since = None
            return "cleared"
        return None


def crash_summary(path):
    """'Description' line and the exception after it from a crash report."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None, None
    for i, line in enumerate(lines):
        if line.startswith("Description: "):
            exception = next((x.strip() for x in lines[i + 1:] if x.strip()), None)
            return line.removeprefix("Description: "), exception
    return None, None


class Monitor:
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.server = bot.server
        repeat = self.cfg.alert_repeat_hours * 3600
        self.down = Problem()
        self.lag = Problem(clear_after=self.cfg.alert_lag_minutes * 60)
        self.backup = Problem(repeat=repeat)
        self.disk = Problem(repeat=repeat)
        self.known_crashes = None

    def alert(self, text, loud=False):
        self.bot.tg.send(self.cfg.admin_id, text, silent=not loud)
        log.info(f"alert: {text}")

    def run(self):
        while True:
            beat("monitor")
            self.check(time.time())
            time.sleep(CHECK_INTERVAL)

    def check(self, now):
        online = self.server.online_players()
        self.check_down(online is not None, now)
        if online is not None:
            self.check_lag(now)
        self.check_crashes()
        self.check_backup(online, now)
        self.check_disk(now)

    def check_down(self, up, now):
        stopping_at = self.server.stopping_at
        minutes = self.cfg.alert_restart_minutes if stopping_at else self.cfg.alert_down_minutes
        if not up and stopping_at and self.down.since is None:
            self.down.since = stopping_at  # a planned stop counts from the stop itself
        event = self.down.update(not up, minutes * 60, now)
        if event == "raised":
            if stopping_at:
                self.alert(f"{e('offline')} <b>Сервер не поднялся после остановки</b>: "
                           f"прошло {fmt_duration(now - stopping_at)}", loud=True)
            else:
                self.alert(f"{e('offline')} <b>Сервер не отвечает</b> уже "
                           f"{fmt_duration(now - self.down.since)}, похоже, упал", loud=True)
        elif event == "cleared":
            self.alert(f"{e('alert_ok')} Сервер снова работает · простой {fmt_duration(self.down.duration)}")

    def check_lag(self, now):
        ts = self.server.tick_stats()
        if ts is None:
            return
        avg, p95 = ts
        mspt = p95 if p95 is not None else avg
        event = self.lag.update(mspt > self.cfg.alert_mspt, self.cfg.alert_lag_minutes * 60, now)
        if event == "raised":
            self.alert(f"{e('alert_lag')} <b>Лаги</b>: MSPT {mspt:.0f} мс (порог {self.cfg.alert_mspt})"
                       f" уже {fmt_duration(now - self.lag.since)}")
        elif event == "cleared":
            self.alert(f"{e('alert_ok')} Лаги прошли · длились {fmt_duration(self.lag.duration)}")

    def check_crashes(self):
        names = {p.name for p in self.server.crash_dir.glob("*.txt")}
        if self.known_crashes is not None:
            for name in sorted(names - self.known_crashes):
                description, exception = crash_summary(self.server.crash_dir / name)
                lines = [f"{e('alert_crash')} <b>Краш сервера</b>"]
                if description:
                    lines.append(html.escape(description))
                if exception:
                    lines.append(f"<code>{html.escape(exception[:300])}</code>")
                lines.append(f"<i>crash-reports/{html.escape(name)}</i>")
                self.alert("\n".join(lines), loud=True)
        self.known_crashes = names

    def check_backup(self, online, now):
        limit = self.cfg.alert_backup_hours * 3600
        age = self.server.last_backup_age()
        if age is not None and age <= limit:
            bad = False
        else:
            names = {n.lower() for n in online[2]} if online else set()
            playing = any(now - t >= PLAYING_FOR_BACKUP for nick, t in self.bot.sessions.items() if nick in names)
            bad = True if playing else None  # nobody playing: mc-backup is paused on purpose
        event = self.backup.update(bad, 0, now)
        if event in ("raised", "repeat"):
            when = f"не делался {fmt_duration(age)}" if age is not None else "ни одного нет"
            self.alert(f"{e('backup')} <b>Бэкап {when}</b>, хотя на сервере играют\n"
                       f"Проверь: <code>docker logs mc-backup</code>")
        elif event == "cleared":
            self.alert(f"{e('alert_ok')} Свежий бэкап появился")

    def check_disk(self, now):
        free = self.server.disk_free()
        event = self.disk.update(free < self.cfg.alert_disk_gb * 1024 ** 3, 0, now)
        if event in ("raised", "repeat"):
            self.alert(f"{e('disk')} <b>Мало места</b>: свободно {fmt_size(free)} (порог {self.cfg.alert_disk_gb} ГБ)")
        elif event == "cleared":
            self.alert(f"{e('alert_ok')} Место есть: свободно {fmt_size(free)}")
