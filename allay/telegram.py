"""Minimal Telegram Bot API client."""

import time

import requests

from .util import log

# pauses between attempts when Telegram or the proxy is unreachable: ~50 s in total,
# enough to ride out a tunnel restart without losing announcements
RETRY_DELAYS = (2, 5, 15, 30)
MAX_RETRY_AFTER = 60  # longer flood-control waits aren't worth blocking a worker for


class Telegram:
    def __init__(self, token, proxy=None):
        self._api = f"https://api.telegram.org/bot{token}/"
        self._session = requests.Session()
        if proxy:
            self._session.proxies = {"https": proxy, "http": proxy}

    def call(self, method, http_timeout=15, retry=True, **params):
        """Returns the result, or None on failure.

        Network errors, 5xx and 429 (flood control) are retried; other API errors are not.
        A retry after a timeout may duplicate a message that did get through.
        """
        delays = list(RETRY_DELAYS) if retry else []
        while True:
            try:
                resp = self._session.post(self._api + method, json=params, timeout=http_timeout)
                data = resp.json()
            except (requests.RequestException, ValueError) as ex:
                error, wait = f"failed: {ex}", delays.pop(0) if delays else None
            else:
                if data.get("ok"):
                    return data["result"]
                code = data.get("error_code", 0)
                if "message is not modified" in data.get("description", ""):
                    return None  # the same button pressed twice
                error = f"error: {data}"
                retry_after = data.get("parameters", {}).get("retry_after")
                if code == 429 and retry_after and retry_after <= MAX_RETRY_AFTER and delays:
                    delays.pop(0)
                    wait = retry_after
                elif code >= 500 and delays:
                    wait = delays.pop(0)
                else:
                    wait = None
            if wait is None:
                log.info(f"telegram {method} {error}")
                return None
            log.info(f"telegram {method} {error}, retrying in {wait}s")
            time.sleep(wait)

    def send(self, chat_id, text, silent=False, reply_to=None, keyboard=None):
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if keyboard:
            params["reply_markup"] = keyboard
        return self.call("sendMessage", **params)

    def edit(self, chat_id, message_id, text, keyboard=None):
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if keyboard:
            params["reply_markup"] = keyboard
        return self.call("editMessageText", **params)


def button(text, data):
    """Inline button; data comes back in callback_query (max 64 bytes)."""
    return {"text": text, "callback_data": data}


def keyboard(buttons, per_row=3, footer=None):
    """Buttons in rows of per_row; footer (e.g. a back button) gets a full-width row of its own."""
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    if footer:
        rows.append([footer])
    return {"inline_keyboard": rows} if rows else None
