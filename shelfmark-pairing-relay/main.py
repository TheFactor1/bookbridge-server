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

  Connecting a new reader to this server (device first, nothing typed on
  the reader):
  GET  /api/hello        -> {"bookbridge": true, "name": ...}  (how a reader
                         recognises this server when it looks for one on its
                         network: it tries this port on each local address)
  POST /api/connect/hello  {"device": "...", "host": "<address it used>"}
                         -> {"code": "K7P4QX", "token": "...", "expires_in": 600}
  GET  /connect[?code=]  the page a person opens on a phone or computer:
                         the code from the reader + the server password
  POST /connect          approves: the reader's token now carries settings
  GET  /api/connect/claim/<token>
                         -> 202 while waiting, 200 {"shelfmark": {...}} once,
                         404 when unknown or expired
  The settings handed over (service addresses on the host the reader used,
  the Shelfmark login, Calibre-Web's, relay tokens) come from the
  environment the installer wrote; approving needs BB_PASSWORD, the server
  password the installer printed. Unset -> connecting is switched off.

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

import hmac
import html
import json
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8086"))
TTL_SECONDS = 5 * 60
CODE_RE = re.compile(r"^[0-9a-f]{8}$")

LOG_DIR = os.environ.get("LOG_DIR")  # unset -> /log is disabled
MAX_LOG_BYTES = 512 * 1024
LOG_RATE_MAX, LOG_RATE_WINDOW = 20, 60.0  # uploads per window, process-wide
_log_times: list[float] = []

_store: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()

# ---- connecting a reader ----------------------------------------------------
BB_PASSWORD = os.environ.get("BB_PASSWORD", "")
SERVER_NAME = os.environ.get("SERVER_NAME", "") or socket.gethostname()
CONNECT_TTL = 10 * 60
CODE_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no O/0/I/1: read off e-ink
_connects: dict[str, dict] = {}     # token -> {code, host, device, expires, settings}
_attempts: list[float] = []         # failed approvals, for a simple rate limit


def _env_port(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def connect_settings(host):
    """What a newly connected reader gets: every enabled service on the host
    the reader reached this one at, plus the logins the installer made."""
    profiles = [p for p in os.environ.get("COMPOSE_PROFILES", "").split(",") if p]
    base = "http://%s:%%d" % host
    out = {
        "server_url": base % _env_port("SHELFMARK_PORT", 8084),
        "pairing_relay_url": base % _env_port("PAIRING_PORT", PORT),
    }
    if os.environ.get("SHELFMARK_USERNAME"):
        out["username"] = os.environ["SHELFMARK_USERNAME"]
        out["password"] = os.environ.get("SHELFMARK_PASSWORD", "")
    if "sync" in profiles:
        out["cwa_url"] = base % _env_port("CWA_PORT", 8083)
        out["cwa_username"] = os.environ.get("CWA_USERNAME", "admin")
        out["cwa_password"] = os.environ.get("CWA_PASSWORD", "admin123")
    if "annas" in profiles:
        out["annas_url"] = base % _env_port("ANNAS_API_PORT", 8087)
    if "ai-local" in profiles or "ai-cloud" in profiles:
        out["ai_relay_url"] = base % _env_port("AI_RELAY_PORT", 8089)
        out["ai_relay_token"] = os.environ.get("RELAY_TOKEN", "")
    return out


def _purge_connects():
    now = time.time()
    for t in [t for t, c in _connects.items() if c["expires"] < now]:
        del _connects[t]


def connect_hello(device, host):
    with _lock:
        _purge_connects()
        if len(_connects) > 50:
            return None
        used = {c["code"] for c in _connects.values()}
        code = "".join(secrets.choice(CODE_ALPHA) for _ in range(6))
        while code in used:
            code = "".join(secrets.choice(CODE_ALPHA) for _ in range(6))
        token = secrets.token_hex(16)
        _connects[token] = {"code": code, "host": host, "device": device,
                            "expires": time.time() + CONNECT_TTL, "settings": None}
    return code, token


def connect_approve(code, password):
    """-> (ok, message)"""
    now = time.time()
    with _lock:
        _attempts[:] = [t for t in _attempts if now - t < 60]
        if len(_attempts) >= 5:
            return False, "Too many tries. Wait a minute and try again."
    if not BB_PASSWORD:
        return False, "Connecting readers is switched off on this server (no BB_PASSWORD set)."
    good = hmac.compare_digest(password.encode(), BB_PASSWORD.encode())
    code = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    with _lock:
        _purge_connects()
        if not good:
            _attempts.append(now)
            return False, "That isn't the server password."
        for c in _connects.values():
            if c["code"] == code and c["settings"] is None:
                c["settings"] = connect_settings(c["host"])
                return True, c["device"] or "your reader"
    return False, "No reader is waiting with that code. Codes last 10 minutes -- start again on the reader."


def connect_claim(token):
    """-> (status, payload)"""
    with _lock:
        _purge_connects()
        c = _connects.get(token)
        if not c:
            return 404, {"error": "unknown or expired"}
        if c["settings"] is None:
            return 202, {"waiting": True, "code": c["code"]}
        del _connects[token]                     # one fetch, ever
        return 200, {"shelfmark": c["settings"]}


CONNECT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect a reader</title>
<style>
 :root{color-scheme:light dark;--ink:#1d1b18;--soft:#6b665f;--bg:#f7f5f0;--card:#fff;--line:#d9d4ca;--ok:#2e6b3a;--bad:#9a2f22}
 @media (prefers-color-scheme:dark){:root{--ink:#ece8e1;--soft:#a39e95;--bg:#191816;--card:#22211e;--line:#3a3833;--ok:#7cc48a;--bad:#e58b7d}}
 body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 system-ui,sans-serif}
 main{max-width:28rem;margin:0 auto;padding:2.5rem 1rem}
 h1{font-size:1.5rem;margin:0 0 .25rem} p{color:var(--soft);margin:.25rem 0 1.5rem}
 form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.25rem}
 label{display:block;font-weight:600;margin:.75rem 0 .3rem}
 input{width:100%%;box-sizing:border-box;font:inherit;padding:.6rem .7rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
 input[name=code]{font:600 1.6rem ui-monospace,monospace;letter-spacing:.3em;text-transform:uppercase}
 button{margin-top:1.25rem;width:100%%;font:600 1rem system-ui;padding:.75rem;border:0;border-radius:6px;background:var(--ink);color:var(--bg)}
 .msg{padding:.8rem 1rem;border-radius:8px;margin-bottom:1rem;border:1px solid var(--line)}
 .ok{color:var(--ok)} .bad{color:var(--bad)}
</style></head><body><main>
<h1>Connect a reader</h1>
<p>to <b>%(name)s</b>. On your reader, choose <i>Connect a book server</i>; it shows a code.</p>
%(msg)s
<form method="post" action="/connect">
 <label for="code">Code on the reader</label>
 <input id="code" name="code" value="%(code)s" maxlength="7" autocomplete="off" autocapitalize="characters" required>
 <label for="pw">Server password</label>
 <input id="pw" name="password" type="password" autocomplete="current-password" required>
 <button type="submit">Connect</button>
</form>
</main></body></html>"""


def connect_page(code="", msg="", ok=None):
    box = ""
    if msg:
        box = '<div class="msg %s">%s</div>' % ("ok" if ok else "bad", html.escape(msg))
    return (CONNECT_PAGE % {"name": html.escape(SERVER_NAME), "msg": box,
                            "code": html.escape(code)}).encode()


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

    def _send_html(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit=4096):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > limit:
            return b""
        return self.rfile.read(length)

    def do_POST(self):
        if self.path == "/log":
            self._receive_log()
            return
        if self.path == "/api/connect/hello":
            try:
                data = json.loads(self._read_body() or b"{}")
            except json.JSONDecodeError:
                data = {}
            device = re.sub(r"[^A-Za-z0-9 ._'-]", "", str(data.get("device", "")))[:40]
            # the address the reader used to reach this server (it has to
            # reach every other service the same way)
            host = str(data.get("host", "")) or (self.headers.get("Host", "").split(":")[0])
            if not re.match(r"^[A-Za-z0-9.-]{1,253}$", host):
                self._send_json(400, {"error": "bad host"})
                return
            made = connect_hello(device, host)
            if not made:
                self._send_json(429, {"error": "too many readers waiting"})
                return
            self._send_json(200, {"code": made[0], "token": made[1], "expires_in": CONNECT_TTL,
                                  "name": SERVER_NAME})
            return
        if self.path == "/connect":
            form = parse_qs(self._read_body().decode("utf-8", "replace"))
            code = (form.get("code") or [""])[0]
            ok, msg = connect_approve(code, (form.get("password") or [""])[0])
            if ok:
                msg = "Connected %s. It finishes on its own in a few seconds." % msg
            self._send_html(200 if ok else 400, connect_page("" if ok else code, msg, ok))
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
        url = urlparse(self.path)
        if url.path in ("/", "/connect"):
            code = re.sub(r"[^A-Za-z0-9]", "", (parse_qs(url.query).get("code") or [""])[0])[:6]
            self._send_html(200, connect_page(code))
            return
        if url.path == "/api/hello":
            self._send_json(200, {"bookbridge": True, "name": SERVER_NAME, "connect": bool(BB_PASSWORD)})
            return
        m = re.match(r"^/api/connect/claim/([0-9a-f]{32})$", url.path)
        if m:
            status, payload = connect_claim(m.group(1))
            self._send_json(status, payload)
            return
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
