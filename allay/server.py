"""Read-only view of the Minecraft server: RCON queries and files in the data dir."""

import hashlib
import json
import os
import re
import time
import uuid

from .util import load_json

LIST_RE = re.compile(r"There are (\d+) of a max of (\d+) players online:?\s*(.*)")


def offline_uuid(nick):
    """Java's UUID.nameUUIDFromBytes("OfflinePlayer:" + nick), as an offline-mode server computes it."""
    h = bytearray(hashlib.md5(f"OfflinePlayer:{nick}".encode()).digest())
    h[6] = h[6] & 0x0F | 0x30  # version 3
    h[8] = h[8] & 0x3F | 0x80  # IETF variant
    return str(uuid.UUID(bytes=bytes(h)))


class Server:
    def __init__(self, rcon, data_dir, backups_dir):
        self.rcon = rcon
        self.data_dir = data_dir
        self.backups_dir = backups_dir
        self.log_file = data_dir / "logs/latest.log"
        self.whitelist_file = data_dir / "whitelist.json"
        self._player_data = data_dir / "world/players/data"
        # new world layout keeps them under players/, older versions in world/advancements
        self._advancement_dirs = [data_dir / "world/players/advancements", data_dir / "world/advancements"]
        self._stats_dirs = [data_dir / "world/players/stats", data_dir / "world/stats"]
        self.started_at = None  # set when "Done (...)" is seen in the log
        self._world_cache = {"at": 0.0, "size": 0}

    # --- rcon ---

    def online_players(self):
        """Returns (count, max, [names]) or None if the server is down."""
        m = LIST_RE.search(self.rcon.command("list") or "")
        if not m:
            return None
        names = [n.strip() for n in m.group(3).split(",") if n.strip()]
        return int(m.group(1)), int(m.group(2)), names

    def tick_stats(self):
        """Returns (avg_ms, p95_ms or None) or None."""
        out = self.rcon.command("tick query") or ""
        avg = re.search(r"Average time per tick: ([\d.]+)ms", out)
        p95 = re.search(r"P95: ([\d.]+)ms", out)
        if not avg:
            return None
        return float(avg.group(1)), float(p95.group(1)) if p95 else None

    def time_query(self, what):
        """`time query day|daytime|gametime` as int, or None."""
        m = re.search(r"The time is (\d+)", self.rcon.command(f"time query {what}") or "")
        return int(m.group(1)) if m else None

    def difficulty(self):
        """'Normal', 'Hard', ... or None."""
        m = re.search(r"The difficulty is (\w+)", self.rcon.command("difficulty") or "")
        return m.group(1) if m else None

    # --- whitelist ---

    def whitelist(self):
        return load_json(self.whitelist_file, [])

    def _write_whitelist(self, entries):
        # in place, not tmp+rename: whitelist.json is a single-file bind mount
        with open(self.whitelist_file, "w") as f:
            json.dump(entries, f, indent=2)
        # None if the server is down: it reads the file on start anyway
        return self.rcon.command("whitelist reload") is not None

    def whitelist_add(self, nick):
        """Adds nick with its offline UUID. Returns (uuid, already_listed, reloaded)."""
        entries = self.whitelist()
        already = any(x.get("name", "").lower() == nick.lower() for x in entries)
        uuid = offline_uuid(nick)
        entries = [x for x in entries if x.get("name", "").lower() != nick.lower()]
        entries.append({"uuid": uuid, "name": nick})
        return uuid, already, self._write_whitelist(entries)

    def whitelist_remove(self, nick):
        """Returns (removed_name or None, reloaded)."""
        entries = self.whitelist()
        kept = [x for x in entries if x.get("name", "").lower() != nick.lower()]
        if len(kept) == len(entries):
            return None, False
        removed = next(x["name"] for x in entries if x.get("name", "").lower() == nick.lower())
        return removed, self._write_whitelist(kept)

    # --- files ---

    def uuid_of(self, nick):
        for entry in self.whitelist():
            if entry.get("name", "").lower() == nick.lower():
                return entry.get("uuid")
        return None

    def advancement_done_by(self, adv_id):
        """UUIDs whose saved advancements file has minecraft:<adv_id> completed.

        Files are saved on autosave/leave, so very fresh progress may be missing.
        """
        key = f"minecraft:{adv_id}"
        done = set()
        for d in self._advancement_dirs:
            for path in d.glob("*.json"):
                entry = load_json(path, {}).get(key)
                if isinstance(entry, dict) and entry.get("done"):
                    done.add(path.stem)
        return done

    def _player_file(self, dirs, uuid):
        for d in dirs:
            path = d / f"{uuid}.json"
            if path.exists():
                return path
        return None

    def player_stats(self, uuid):
        """Raw stats json, or None if the player has never played."""
        path = self._player_file(self._stats_dirs, uuid)
        return load_json(path, None) if path else None

    def advancement_count(self, uuid):
        """Completed advancements, recipes excluded."""
        path = self._player_file(self._advancement_dirs, uuid)
        data = load_json(path, {}) if path else {}
        return sum(1 for k, v in data.items()
                   if isinstance(v, dict) and v.get("done") and not k.startswith("minecraft:recipes/"))

    def version(self):
        """Minecraft version from the current log, or None."""
        try:
            with open(self.log_file, encoding="utf-8", errors="replace") as f:
                for _, line in zip(range(500), f):  # it's near the top
                    m = re.search(r"Starting minecraft server version (\S+)", line)
                    if m:
                        return m.group(1)
        except OSError:
            pass
        return None

    def last_seen(self, uuid):
        try:
            return (self._player_data / f"{uuid}.dat").stat().st_mtime
        except OSError:
            return None

    def world_size(self):
        if time.time() - self._world_cache["at"] > 600:  # walking the world is slow-ish, cache 10 min
            total = 0
            for root, _, files in os.walk(self.data_dir / "world"):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        pass
            self._world_cache.update(at=time.time(), size=total)
        return self._world_cache["size"]

    def disk_free(self):
        st = os.statvfs(self.data_dir)
        return st.f_bavail * st.f_frsize

    def last_backup_age(self):
        files = [p for p in self.backups_dir.glob("*") if p.is_file()]
        if not files:
            return None
        return time.time() - max(p.stat().st_mtime for p in files)
