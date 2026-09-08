# Shelfmark server stack

Everything the [Shelfmark KOReader plugin](https://github.com/TheFactor1/koreader-shelfmark-plugin) can
talk to, in one `docker-compose.yml`. Run it on a machine at home; point the
plugin at that machine.

**You need a computer that runs Docker and stays on.** That is the one thing
this can't do for you. Everything else is a few lines in one file.

## Easiest: the setup wizard

```bash
HOST_ADDRESS=$(tailscale ip -4 2>/dev/null | head -1) \
  docker compose -f docker-compose.setup.yml up -d
# then open http://<this machine>:8090
```

A browser page walks you through it: pick what to run, it writes the config,
starts the services, tests them, and shows a code your Kindle claims under
**Shelfmark → Settings → Import from server**. When you're done you can stop
the wizard (`docker compose -f docker-compose.setup.yml down`) — the services
keep running.

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
| `sync`     | calibre-web-automated         | Library sync; tap-to-download of delivered books       | Nothing; set `CALIBRE_LIBRARY` to use an existing library |
| `annas`    | annas-archive-api             | Anna's Archive as the primary search source            | An Anna's Archive account key, entered in the plugin |
| `ai-local` | shelfmark-ai-relay + ollama   | Match suggestions after a sync, using a local model    | `RELAY_TOKEN`; ~8 GB free RAM; a ~5 GB model download |
| `ai-cloud` | shelfmark-ai-relay            | Same, using an API key instead of a local model        | `RELAY_TOKEN`, `AI_PROVIDER`, `AI_API_KEY`        |

Shelfmark and CWA hand books to each other through a shared `ingest` volume.
Request on the Kindle → Shelfmark fetches → drops it in `ingest` → CWA imports
it → it appears in the library the plugin syncs from. That mount is why `sync`
works at all; don't replace it with two separate host paths.

## Reaching it from the Kindle

The plugin talks to these ports directly — none of this sits behind a
reverse proxy. Two ways to make `<this machine>` reachable from the reader:

- **Tailscale (recommended).** Install Tailscale on this machine and on
  nothing else; Docker publishes the ports on the host, so the Kindle
  reaches them at the machine's `100.x.x.x` address. Private by default,
  no domain needed. Enter that address in the plugin.
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

Change any of them in `.env` if something on your machine already uses it.

## Status

Two of the images here — `annas-archive-api` and `shelfmark-ai-relay` — are
built and published by this repository's own workflow
(`.github/workflows/publish-images.yml`, run by hand). Until the first run,
`docker compose pull` can't fetch them and they build from the source
directories beside this file — so the stack works for anyone with this
repository, and not yet for anyone without it.

## Credits

This stack only arranges other people's software:
[Shelfmark](https://github.com/calibrain/shelfmark) by calibrain,
[Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated)
by crocodilestick, [KOReader](https://github.com/koreader/koreader), and
[Anna's Archive](https://annas-archive.org) (searched by the `annas-archive-api`
service beside this file). The compose file, the relay services and this
document are the only original parts.

## Authorship

**Everything in this directory was written by an AI** (Claude, by Anthropic),
with Matt directing, running it on his own server and devices, and deciding
what to keep. Read it before you rely on it.
