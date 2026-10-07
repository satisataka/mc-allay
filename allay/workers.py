"""Long-running background loops."""

import json
import os
import threading
import time

from .util import log


heartbeats = {}  # worker name -> when it last proved to be alive, see the watchdog in __main__


def beat(name):
    heartbeats[name] = time.time()


def stale_workers(limit):
    """Workers that haven't beaten for `limit` seconds: stuck, e.g. on a hung socket."""
    now = time.time()
    return sorted(name for name, at in heartbeats.items() if now - at > limit)


def run_forever(fn, name):
    def wrapper():
        while True:
            try:
                fn()
            except Exception:
                log.exception(f"{name} crashed, restarting in 10s")
                time.sleep(10)
    threading.Thread(target=wrapper, name=name, daemon=True).start()


class Batcher:
    """Groups items by key: flush(key, items) runs `delay` seconds after the key's first item."""

    def __init__(self, delay, flush):
        self.delay = delay
        self.flush = flush
        self._pending = {}
        self._lock = threading.Lock()

    def add(self, key, item):
        with self._lock:
            if key not in self._pending:
                self._pending[key] = []
                timer = threading.Timer(self.delay, self._fire, [key])
                timer.daemon = True
                timer.start()
            self._pending[key].append(item)

    def _fire(self, key):
        with self._lock:
            items = self._pending.pop(key, [])
        try:
            self.flush(key, items)
        except Exception:
            log.exception(f"batch flush for {key} failed")


def follow_log(path, on_line):
    """tail -F: calls on_line for every new complete line, survives log rotation."""
    first_open = True
    while True:
        beat("follow_log")
        try:
            f = open(path, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            time.sleep(5)
            continue
        with f:
            inode = os.fstat(f.fileno()).st_ino
            if first_open:
                f.seek(0, os.SEEK_END)  # don't replay history on bot start
                first_open = False
            log.info(f"watching {path}")
            buf = ""
            while True:
                beat("follow_log")
                chunk = f.readline()
                if chunk:
                    buf += chunk
                    if buf.endswith("\n"):
                        try:
                            on_line(buf.rstrip("\n"))
                        except Exception:
                            log.exception("log line handler error")
                        buf = ""
                    continue
                time.sleep(0.5)
                try:
                    st = os.stat(path)
                except FileNotFoundError:
                    continue
                if st.st_ino != inode or st.st_size < f.tell():
                    break  # rotated on server restart: reopen and read the new file from the start


def watch_whitelist(path, on_added):
    """Polls whitelist.json and calls on_added(nick) for every newly added player."""
    known = None  # lowercased nick -> nick
    while True:
        beat("watch_whitelist")
        try:
            names = {x["name"].lower(): x["name"] for x in json.loads(path.read_text())}
        except (OSError, ValueError, KeyError, TypeError):
            time.sleep(5)  # missing or being written right now
            continue
        if known is None:
            log.info(f"whitelist baseline: {len(names)} players")
        else:
            for key in sorted(names.keys() - known.keys()):
                on_added(names[key])
        known = names
        time.sleep(5)
