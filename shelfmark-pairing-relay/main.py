"""Ephemeral, one-time pairing store for shelfmark.koplugin's device-to-device
settings transfer (the "Show setup QR code" / "Import settings" feature).

This service never sees a plaintext setting or password -- the plugin
encrypts the settings blob client-side with a one-time pad (a fresh random
key exactly as long as the message, generated from /dev/urandom, XORed with
the plaintext -- see the note in main.lua for why that's a real, correctly-
implementable substitute for a proper cipher when no crypto library exists
in KOReader's Lua environment) before it ever reaches here. This service
only ever stores and serves ciphertext, keyed by a random code; the key
that actually decrypts it travels separately, folded into the same QR code/
pairing text, and never touches this service at all.

  POST /pair            body: {"ciphertext": "<base64>"}
                         -> {"code": "<random hex code>"}
  GET  /pair/<code>     -> {"ciphertext": "<base64>"}, and the entry is
                         deleted immediately -- one fetch, ever, is what
                         "one-time pad" and "one-time pairing" both mean
                         here. 404 if the code doesn't exist or has expired.

  POST /log             body: the plugin's debug log, plain text
                         -> {"id": "<filename it was saved under>"}
                         Files it under LOG_DIR on the host for the operator
                         to read. Never served back over HTTP. Exists so a
                         problem seen on a phone can be read on the homeserver
                         with one tap in the plugin -- no adb, no screenshots
                         of the log viewer. Disabled (404) unless LOG_DIR is
                         set. Capped at 512 KB and 20 uploads a minute so a
                         misbehaving client cannot fill the disk.

No authentication -- by design. Anyone who could authenticate would need a
credential pre-shared over some other channel, at which point they could
exchange the settings directly and skip this whole feature. Security here
comes from three things instead: the code is unguessable in the time it
matters (8 random hex chars = 32 bits, and reachable only over
Tailscale/LAN, not the public internet), entries expire after 5 minutes
even if never fetched, and a successful guess of the code alone still only
yields ciphertext -- the decryption key never passes through this service.
"""

import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8086"))
TTL_SECONDS = 5 * 60
CODE_RE = re.compile(r"^[0-9a-f]{8}$")

LOG_DIR = os.environ.get("LOG_DIR")  # unset -> /log is disabled
MAX_LOG_BYTES = 512 * 1024
LOG_RATE_MAX, LOG_RATE_WINDOW = 20, 60.0  # uploads per window, process-wide
_log_times: list[float] = []

_store: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def _purge_expired() -> None:
    now = time.monotonic()
    expired = [code for code, (_, expires_at) in _store.items() if expires_at <= now]
    for code in expired:
        del _store[code]


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/log":
            self._receive_log()
            return
        if self.path != "/pair":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return
        ciphertext = data.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            self._send_json(400, {"error": "ciphertext is required"})
            return
        if len(ciphertext) > 8192:
            # The settings blob this carries is a handful of short fields
            # -- comfortably under 1KB even base64-inflated. Anything this
            # much larger isn't a legitimate use of this endpoint.
            self._send_json(413, {"error": "ciphertext too large"})
            return

        code = secrets.token_hex(4)
        with _lock:
            _purge_expired()
            # Collision odds at 32 bits of randomness are negligible, but
            # loop rather than silently overwrite an unrelated pending
            # pairing on the one-in-four-billion chance it happens anyway.
            while code in _store:
                code = secrets.token_hex(4)
            _store[code] = (ciphertext, time.monotonic() + TTL_SECONDS)
        self._send_json(200, {"code": code})

    def _receive_log(self):
        if not LOG_DIR:
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            self._send_json(400, {"error": "empty"})
            return
        if length > MAX_LOG_BYTES:
            self._send_json(413, {"error": "log too large"})
            return
        now = time.monotonic()
        with _lock:
            _log_times[:] = [t for t in _log_times if now - t < LOG_RATE_WINDOW]
            if len(_log_times) >= LOG_RATE_MAX:
                self._send_json(429, {"error": "too many uploads, try again in a minute"})
                return
            _log_times.append(now)
        raw = self.rfile.read(length)
        # Timestamp + a little randomness: unique without trusting anything
        # from the client for the filename.
        name = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3) + ".log"
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, name), "wb") as f:
            f.write(raw)
        self._send_json(200, {"id": name})

    def do_GET(self):
        match = re.match(r"^/pair/([0-9a-f]{8})$", self.path)
        if not match:
            self._send_json(404, {"error": "not found"})
            return
        code = match.group(1)
        with _lock:
            _purge_expired()
            entry = _store.pop(code, None)
        if entry is None:
            self._send_json(404, {"error": "not found or already used"})
            return
        ciphertext, _expires_at = entry
        self._send_json(200, {"ciphertext": ciphertext})

    def log_message(self, format_str, *args):
        pass  # no request bodies/codes in logs -- nothing sensitive to log anyway, but no reason to


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
