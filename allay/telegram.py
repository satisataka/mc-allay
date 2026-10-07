"""Minimal Telegram Bot API client."""

import requests

from .util import log


class Telegram:
    def __init__(self, token, proxy=None):
        self._api = f"https://api.telegram.org/bot{token}/"
        self._session = requests.Session()
        if proxy:
            self._session.proxies = {"https": proxy, "http": proxy}

    def call(self, method, http_timeout=15, **params):
        try:
            resp = self._session.post(self._api + method, json=params, timeout=http_timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as ex:
            log.info(f"telegram {method} failed: {ex}")
            return None
        if not data.get("ok"):
            log.info(f"telegram {method} error: {data}")
            return None
        return data["result"]

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
