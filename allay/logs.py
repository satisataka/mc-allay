"""Server log lines: parsing events and recovering real timestamps.

Lines carry only a clock ("[14:03:12]"), so the date is reconstructed:
- latest.log: its last line was written at the file's mtime, earlier lines are
  placed relative to it (no timezone assumptions needed);
- archives (logs/2026-10-07-1.log.gz): the date comes from the name, the
  timezone of the clock is estimated from latest.log.
"""

import gzip
import re
from datetime import datetime, timezone

NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
LOG_RE = re.compile(r"^\[[^\]]+\] \[Server thread/INFO\]: (.*)$")
CLOCK_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]")
ARCHIVE_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)-(\d+)\.log\.gz$")
DAY = 86400


def log_event(line):
    """('join' | 'leave', nick), ('done' | 'stopping', None), ('system' | 'message', text), or None."""
    m = LOG_RE.match(line)
    if not m:
        return None
    raw = m.group(1)
    msg = raw.removeprefix("System chat: ")
    for suffix, kind in ((" joined the game", "join"), (" left the game", "leave")):
        if msg.endswith(suffix):
            nick = msg[: -len(suffix)]
            # rejects chat lines like "<Ruslan> x joined the game"
            return (kind, nick) if NICK_RE.match(nick) else None
    if msg.startswith("Done ("):
        return "done", None
    if msg == "Stopping server":  # a planned stop, unlike a crash
        return "stopping", None
    return ("system" if raw.startswith("System chat: ") else "message"), msg


def clock_of(line):
    """Seconds since midnight from the "[HH:MM:SS]" prefix, or None."""
    m = CLOCK_RE.match(line)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def _unwrap(lines):
    """[(seconds since the first line's midnight, line)]; the clock jumping back means the next day."""
    out, day, prev = [], 0, None
    for line in lines:
        line = line.rstrip("\n")
        clock = clock_of(line)
        if clock is None:
            continue  # stack traces and other continuation lines
        if prev is not None and clock < prev - DAY // 2:  # a small step back is clock jitter, not midnight
            day += 1
        prev = clock
        out.append((day * DAY + clock, line))
    return out


def read_latest(path):
    """[(epoch, line)] for latest.log."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = _unwrap(f)
    if not lines:
        return []
    mtime = path.stat().st_mtime
    last = lines[-1][0]
    return [(mtime - (last - t), line) for t, line in lines]


def utc_offset(epoch, line):
    """Offset of the log clock from UTC, in seconds rounded to 15 min, from one timed line."""
    diff = (clock_of(line) - epoch) % DAY
    if diff > DAY // 2:
        diff -= DAY
    return round(diff / 900) * 900


def read_archive(path, offset):
    """[(epoch, line)] for a gzipped archive; offset is the log clock's offset from UTC."""
    m = ARCHIVE_RE.match(path.name)
    if not m:
        return []
    y, mo, d, _ = map(int, m.groups())
    midnight = datetime(y, mo, d, tzinfo=timezone.utc).timestamp() - offset
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        return [(midnight + t, line) for t, line in _unwrap(f)]


def archives(logs_dir):
    """Archived logs, newest first."""
    found = []
    for path in logs_dir.glob("*.log.gz"):
        m = ARCHIVE_RE.match(path.name)
        if m:
            found.append((tuple(map(int, m.groups())), path))
    return [path for _, path in sorted(found, reverse=True)]
