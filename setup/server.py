#!/usr/bin/env python3
"""Shelfmark setup wizard -- the one page that stands up the stack.

A small stdlib HTTP server (no framework, same idiom as shelfmark-ai-relay).
It runs alongside Docker, holds the stack directory and the Docker socket, and
walks a person through: answer a few questions -> it writes the stack's .env
and picks the compose profiles -> it starts the chosen services -> it tests
each one -> it shows a pairing code the Kindle claims.

Endpoints
  GET  /                     the wizard UI
  GET  /health               {"ok": true}
  GET  /api/state            what is currently configured + running
  POST /api/configure        {answers} -> writes <stack>/.env, returns summary
  POST /api/start            docker compose up -d for the chosen profiles
  GET  /api/test             per-service reachability, plain-language
  POST /api/pair             mint a single-use, 10-min claim code for the Kindle
  GET  /claim/<code>         the Kindle fetches its settings once, then it's gone

Environment
  STACK_DIR        where docker-compose.yml + .env live (default /stack)
  SETUP_PORT       listen port (default 8090)
  HOST_ADDRESS     optional prefill for the address the Kindle will use

Security note: this holds the Docker socket, which is root on the host. It is
meant to run only during first-run and be stopped afterwards (the wizard's
last screen offers to). A socket proxy limiting it to the compose calls it
makes is the documented next hardening step; see README.
"""
import json
import os
import re
import secrets
import subprocess
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STACK_DIR = os.environ.get("STACK_DIR", "/stack")
PORT = int(os.environ.get("SETUP_PORT", "8090"))
HOST_ADDRESS = os.environ.get("HOST_ADDRESS", "")
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

# Which optional features map to which compose profile and which services they
# turn on. The single source of truth the UI and the writer both read from.
FEATURES = {
    "sync":  {"profile": "sync",  "label": "Library sync (Calibre-Web-NextGen)"},
    "annas": {"profile": "annas", "label": "Anna's Archive as the primary source"},
    # The compose file has two AI profiles; which one depends on the answers
    # (see ai_profile). A literal "ai" profile starts nothing.
    "ai":    {"profile": "ai-cloud", "label": "AI match suggestions"},
}
# Services and the host port each answers on, for the reachability test. Keyed
# to what the compose file publishes.
SERVICE_PORTS = {
    "shelfmark": 8084,
    "cwa": 8083,
    "annas-archive-api": 8087,
    "shelfmark-ai-relay": 8089,
    "shelfmark-pairing-relay": 8086,
}


def ai_profile(answers):
    """ai-local runs a model on this machine (ollama); ai-cloud uses an API key."""
    return "ai-cloud" if (answers.get("ai_api_key") or "").strip() else "ai-local"

# ---- claim codes (the Kindle pairing) -------------------------------------
# A claim is the plugin's settings, held briefly under a short code the person
# types on the reader. Single use, ten-minute expiry, in memory only -- the
# setup container is ephemeral, and a code that outlived a restart would be a
# standing credential for no reason.
_claims = {}          # code -> {"settings": {...}, "expires": epoch}
_claims_lock = threading.Lock()
CLAIM_TTL = 600


def _prune_claims():
    now = time.time()
    with _claims_lock:
        for c in [c for c, v in _claims.items() if v["expires"] < now]:
            del _claims[c]


def mint_claim(settings):
    _prune_claims()
    # 6 chars, unambiguous alphabet (no O/0/I/1) -- it gets typed on an e-ink
    # keyboard, so legibility beats entropy; single-use + short TTL cover the
    # rest.
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alpha) for _ in range(6))
    with _claims_lock:
        _claims[code] = {"settings": settings, "expires": time.time() + CLAIM_TTL}
    return code


def take_claim(code):
    _prune_claims()
    with _claims_lock:
        entry = _claims.pop(code.upper(), None)   # pop = single use
    if not entry or entry["expires"] < time.time():
        return None
    return entry["settings"]


# ---- .env writing ----------------------------------------------------------
ENV_PATH = os.path.join(STACK_DIR, ".env")

# Only these keys are ever written, and only from a fixed vocabulary -- the
# wizard never passes arbitrary text through to a file compose will source.
ENV_KEYS = [
    "COMPOSE_PROFILES", "TZ", "PUID", "PGID",
    "CALIBRE_LIBRARY", "HARDCOVER_TOKEN",
    "RELAY_TOKEN", "AI_PROVIDER", "AI_API_BASE", "AI_MODEL", "AI_API_KEY",
]
_SAFE = re.compile(r"^[A-Za-z0-9 _./:@,+-]*$")


def write_env(values):
    """Write a minimal .env from a whitelisted, validated dict."""
    lines = ["# Written by the Shelfmark setup wizard. Re-run the wizard to change."]
    for k in ENV_KEYS:
        v = values.get(k, "")
        if v is None:
            v = ""
        v = str(v)
        if not _SAFE.match(v):
            raise ValueError("invalid characters in %s" % k)
        lines.append("%s=%s" % (k, v))
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ENV_PATH)


def read_env():
    out = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


# ---- docker compose --------------------------------------------------------
def compose(*args, timeout=300):
    """Run `docker compose` against the stack, returning (rc, combined output)."""
    cmd = ["docker", "compose", "-f", os.path.join(STACK_DIR, "docker-compose.yml")]
    cmd += list(args)
    try:
        p = subprocess.run(cmd, cwd=STACK_DIR, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, "docker CLI not found in the setup container"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def running_services():
    rc, out = compose("ps", "--format", "{{.Service}}", timeout=30)
    if rc != 0:
        return []
    return [s for s in out.splitlines() if s.strip()]


# ---- reachability test -----------------------------------------------------
def test_service(name, port):
    """Reach a service on the host by its published port. From inside this
    container 'host.docker.internal' maps to the host; fall back to the
    gateway. Returns (ok, detail)."""
    import http.client
    for host in ("host.docker.internal", _gateway_ip()):
        if not host:
            continue
        try:
            conn = http.client.HTTPConnection(host, port, timeout=4)
            path = "/health" if name == "shelfmark-ai-relay" else "/"
            conn.request("GET", path)
            r = conn.getresponse()
            code = r.status
            conn.close()
            if 200 <= code < 500:   # anything that answers HTTP is "up"
                return True, "answered HTTP %d" % code
        except Exception:
            continue
    return False, "no response on port %d" % port


def _gateway_ip():
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if fields[1] == "00000000":
                    return ".".join(str(int(fields[2][i:i+2], 16)) for i in (6, 4, 2, 0))
    except Exception:
        pass
    return None


# ---- HTTP handler ----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "shelfmark-setup"

    def log_message(self, *a):
        pass  # quiet; this runs briefly and interactively

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # -- GET --
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/" or path == "/index.html":
            return self._serve_ui("index.html")
        if path.startswith("/claim/"):
            return self._handle_claim(path[len("/claim/"):])
        if path == "/api/state":
            return self._send(200, {
                "configured": os.path.exists(ENV_PATH),
                "env": {k: v for k, v in read_env().items() if "TOKEN" not in k and "KEY" not in k},
                "running": running_services(),
                "features": FEATURES,
                "host_address": HOST_ADDRESS,
            })
        if path == "/api/test":
            return self._handle_test()
        if path.startswith("/ui/"):
            return self._serve_ui(path[len("/ui/"):])
        return self._send(404, {"error": "not found"})

    # -- POST --
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/configure":
            return self._handle_configure(self._read_json())
        if path == "/api/start":
            return self._handle_start(self._read_json())
        if path == "/api/pair":
            return self._handle_pair(self._read_json())
        return self._send(404, {"error": "not found"})

    # -- handlers --
    def _serve_ui(self, name):
        name = os.path.basename(name)
        full = os.path.join(UI_DIR, name)
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain")
        ctype = "text/html" if name.endswith(".html") else "text/plain"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _handle_configure(self, answers):
        # Build the env from a strict, validated set of answers.
        features = [f for f in answers.get("features", []) if f in FEATURES]
        profiles = ",".join(ai_profile(answers) if f == "ai" else FEATURES[f]["profile"] for f in features)
        calibre = answers.get("calibre_library", "")
        # An absolute default, not the compose file's relative ./calibre-library:
        # compose runs against the host daemon from inside this container, and a
        # relative bind path would resolve to a directory that only exists in
        # here. STACK_DIR is mounted at its real host path (see the setup
        # compose), so joining onto it gives a path the daemon resolves the same
        # way on the host.
        if "sync" in features and not calibre:
            calibre = os.path.join(STACK_DIR, "calibre-library")
        values = {
            "COMPOSE_PROFILES": profiles,
            "TZ": answers.get("tz", "UTC"),
            "PUID": answers.get("puid", "1000"),
            "PGID": answers.get("pgid", "1000"),
            "CALIBRE_LIBRARY": calibre,
            "HARDCOVER_TOKEN": answers.get("hardcover_token", ""),
            "RELAY_TOKEN": answers.get("relay_token", ""),
            "AI_PROVIDER": answers.get("ai_provider", "openai"),
            "AI_API_BASE": answers.get("ai_api_base", "http://ollama:11434/v1"),
            "AI_MODEL": answers.get("ai_model", "qwen2.5:7b-instruct"),
            "AI_API_KEY": answers.get("ai_api_key", ""),
        }
        if "ai" in features and not values["RELAY_TOKEN"]:
            values["RELAY_TOKEN"] = secrets.token_hex(24)
        try:
            write_env(values)
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        return self._send(200, {"ok": True, "profiles": profiles,
                                 "relay_token": values["RELAY_TOKEN"] if "ai" in features else ""})

    def _handle_start(self, body):
        env = read_env()
        profiles = env.get("COMPOSE_PROFILES", "")
        args = []
        for p in [x for x in profiles.split(",") if x]:
            args += ["--profile", p]
        rc, out = compose(*(args + ["up", "-d"]), timeout=600)
        return self._send(200 if rc == 0 else 500, {"ok": rc == 0, "output": out[-4000:]})

    def _handle_test(self):
        env = read_env()
        profiles = [x for x in env.get("COMPOSE_PROFILES", "").split(",") if x]
        want = {"shelfmark", "shelfmark-pairing-relay"}
        if "sync" in profiles:
            want.add("cwa")
        if "annas" in profiles:
            want.add("annas-archive-api")
        if "ai-local" in profiles or "ai-cloud" in profiles:
            want.add("shelfmark-ai-relay")
        results = {}
        for name in sorted(want):
            ok, detail = test_service(name, SERVICE_PORTS[name])
            results[name] = {"ok": ok, "detail": detail}
        return self._send(200, {"results": results})

    def _handle_pair(self, body):
        addr = (body.get("address") or HOST_ADDRESS or "").strip()
        if not addr:
            return self._send(400, {"error": "no address given"})
        # scheme-less host[:port] -> http:// for the LAN/tailnet services
        base = addr if "://" in addr else "http://" + addr
        host = urlparse(base).hostname or addr
        env = read_env()
        profiles = [x for x in env.get("COMPOSE_PROFILES", "").split(",") if x]
        # The plugin settings, pointing every enabled service at this host.
        settings = {"server_url": "http://%s:%d" % (host, SERVICE_PORTS["shelfmark"]),
                    "pairing_relay_url": "http://%s:%d" % (host, SERVICE_PORTS["shelfmark-pairing-relay"])}
        if "sync" in profiles:
            settings["cwa_url"] = "http://%s:%d" % (host, SERVICE_PORTS["cwa"])
            # Calibre-Web-NextGen ships with admin/admin123; carry that so the plugin works out
            # of the box, and let the wizard override it if the person changed
            # it. Not a secret this server invented -- it's CWA's own default.
            settings["cwa_username"] = (body.get("cwa_username") or "admin").strip()
            settings["cwa_password"] = body.get("cwa_password") or "admin123"
        if "annas" in profiles:
            settings["annas_url"] = "http://%s:%d" % (host, SERVICE_PORTS["annas-archive-api"])
        if "ai-local" in profiles or "ai-cloud" in profiles:
            settings["ai_relay_url"] = "http://%s:%d" % (host, SERVICE_PORTS["shelfmark-ai-relay"])
            settings["ai_relay_token"] = env.get("RELAY_TOKEN", "")
        code = mint_claim(settings)
        return self._send(200, {"code": code, "expires_in": CLAIM_TTL,
                                "services": list(settings.keys())})

    def _handle_claim(self, code):
        code = re.sub(r"[^A-Za-z0-9]", "", code)[:6]
        settings = take_claim(code)
        if settings is None:
            return self._send(404, {"error": "no such code, or it expired"})
        return self._send(200, {"shelfmark": settings})


def main():
    os.makedirs(STACK_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("shelfmark-setup listening on :%d, stack=%s" % (PORT, STACK_DIR), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
