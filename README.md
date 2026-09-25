# Shelfmark server stack

Everything the [Bookbridge KOReader plugin](https://github.com/TheFactor1/koreader-bookbridge-plugin) (formerly the Shelfmark plugin) can
talk to, in one `docker-compose.yml`. Run it on a machine at home; point the
plugin at that machine.

**You need a computer that runs Docker and stays on.** That is the one thing
this can't do for you. Everything else is a few lines in one file.

## Easiest: the setup wizard

The full walkthrough, including the reader side, is in the
[Bookbridge README](https://github.com/TheFactor1/koreader-bookbridge-plugin#set-it-up-step-by-step).
The server part in short (Linux or Mac; on Windows use WSL):

1. Install [Docker](https://docs.docker.com/get-docker/) and, if you want to
   reach it away from home, [Tailscale](https://tailscale.com/download).
2. Get these files and start the wizard:
   ```bash
   git clone https://github.com/TheFactor1/shelfmark-stack
   cd shelfmark-stack
   docker compose -f docker-compose.setup.yml up -d
   ```
3. Open **http://localhost:8090** and follow its five parts: this machine's
   address, what to run, **Configure & start**, **Check the services**, and
   **Pair your Kindle**, which shows a 6-character code (valid 10 minutes).
4. Open **http://localhost:8084** and make your Shelfmark account.
5. On the reader: **Bookbridge > Status & setup > Start here**, enter the
   address and the code.

When you're done, stop the wizard -- the services keep running:
`docker compose -f docker-compose.setup.yml down`

The wizard holds the Docker socket (root on the host) so it can start the
stack for you; it is meant to run only during setup. A socket proxy limiting
it to just the calls it makes is a planned hardening step.

## Or by hand

```bash
cp .env.example .env      # then open .env and fill in what applies
docker compose up -d
```

With `.env` untouched, that starts **Shelfmark alone** — search and request
books from the Kindle. That is the only required piece. Open
`http://<this machine>:8084` to finish Shelfmark's own first-run setup.

## Add features

Set `COMPOSE_PROFILES` in `.env` and run `docker compose up -d` again. Nothing
else changes.

| Profile    | Adds                          | What the plugin gains                                  | Needs                                             |
|------------|-------------------------------|--------------------------------------------------------|---------------------------------------------------|
| `sync`     | Calibre-Web-NextGen           | Library sync; tap-to-download of delivered books       | Nothing; set `CALIBRE_LIBRARY` to use an existing library. First login admin / admin123 -- change it |
| `annas`    | annas-archive-api             | Anna's Archive as the primary search source            | An Anna's Archive account key, entered in the plugin |
| `ai-local` | shelfmark-ai-relay + ollama   | Match suggestions after a sync, using a local model    | `RELAY_TOKEN`; ~8 GB free RAM; a ~5 GB model download |
| `ai-cloud` | shelfmark-ai-relay            | Same, using an API key instead of a local model        | `RELAY_TOKEN`, `AI_PROVIDER`, `AI_API_KEY`        |

Always on, whatever the profiles: **shelfmark-pairing-relay** (tiny), which the
plugin's *Set up another device* (copy settings to a second reader by QR code)
and *Send debug log to server* use. Debug logs land in its `pairing_logs`
volume for you to read; nothing is ever served back.

Calibre-Web-NextGen is a maintained fork of Calibre-Web-Automated (same
volumes, ports and first login); the upstream stopped releasing after v4.0.6
with security fixes left unreleased.

Shelfmark and Calibre-Web hand books to each other through a shared `ingest` volume.
Request on the Kindle → Shelfmark fetches → drops it in `ingest` → CWA imports
it → it appears in the library the plugin syncs from. That mount is why `sync`
works at all; don't replace it with two separate host paths.

## Reaching it from the Kindle

The plugin talks to these ports directly — none of this sits behind a
reverse proxy. Two ways to make `<this machine>` reachable from the reader:

- **Tailscale (recommended).** Install Tailscale on this machine. On a
  Kindle or Kobo, also install the Tailscale VPN KOReader plugin (by
  Jadehawk); Bookbridge finds its local proxy by itself. Docker publishes the
  ports on the host, so the reader reaches them at the machine's `100.x.x.x`
  address. Private by default, no domain needed. The wizard uses that
  address when it builds the reader's settings.
- **Cloudflare Tunnel.** Public `https://` hostnames, reachable from
  anywhere with no client on the Kindle. Needs a domain on Cloudflare. The
  services then rely on their own logins; putting Cloudflare Access in
  front requires service tokens in the plugin (planned, not yet built).

## Ports

| Service            | Host port (default) |
|--------------------|---------------------|
| shelfmark          | 8084                |
| cwa                | 8083                |
| annas-archive-api  | 8087                |
| shelfmark-ai-relay | 8089                |
| shelfmark-pairing-relay | 8086           |

Change any of them in `.env` if something on your machine already uses it.

## Status

Four of the images here — `annas-archive-api`, `shelfmark-ai-relay`,
`shelfmark-pairing-relay` and the setup wizard — are built and published by
this repository's own workflow
(`.github/workflows/publish-images.yml`, run by hand). Until the first run,
`docker compose pull` can't fetch them and they build from the source
directories beside this file, so a clone of this repository always works.

## Credits

This stack only arranges other people's software:
[Shelfmark](https://github.com/calibrain/shelfmark) by calibrain,
[Calibre-Web-NextGen](https://github.com/new-usemame/Calibre-Web-NextGen) by
new-usemame, a fork of
[Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated)
by crocodilestick, [KOReader](https://github.com/koreader/koreader), and
[Anna's Archive](https://annas-archive.org) (searched by the `annas-archive-api`
service beside this file). The compose file, the relay services and this
document are the only original parts.

## Authorship

**Everything in this directory was written by an AI** (Claude, by Anthropic),
with Matt directing, running it on his own server and devices, and deciding
what to keep. Read it before you rely on it.
