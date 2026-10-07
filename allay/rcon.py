"""Minecraft RCON client."""

import re
import socket
import struct

from .util import log

_AUTH = 3
_EXEC = 2


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("rcon connection closed")
        buf += chunk
    return buf


def _packet(req_id, ptype, body):
    payload = struct.pack("<ii", req_id, ptype) + body.encode() + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _read(sock):
    size = struct.unpack("<i", _recv_exact(sock, 4))[0]
    data = _recv_exact(sock, size)
    req_id = struct.unpack("<i", data[:4])[0]
    return req_id, data[8:-2].decode("utf-8", "replace")


class Rcon:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self._password = password

    def command(self, command):
        """Run one RCON command. Returns response text, or None if the server is unreachable."""
        try:
            with socket.create_connection((self.host, self.port), timeout=5) as sock:
                sock.sendall(_packet(1, _AUTH, self._password))
                if _read(sock)[0] == -1:
                    log.info("rcon auth failed")
                    return None
                sock.sendall(_packet(2, _EXEC, command))
                return re.sub(r"§.", "", _read(sock)[1])  # strip color codes
        except (OSError, struct.error) as ex:
            log.info(f"rcon '{command}' failed: {ex}")
            return None
