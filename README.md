# Bookbridge server

*Formerly `shelfmark-stack` -- old links and clones still work; GitHub redirects them.*

Everything the [Bookbridge KOReader plugin](https://github.com/TheFactor1/koreader-bookbridge-plugin)
talks to: search and
request books from your reader, and keep your library in sync with it.

**You need a computer that stays on** -- Linux, a Mac, or Windows with WSL
(with WSL's [mirrored networking](https://learn.microsoft.com/windows/wsl/networking#mirrored-mode-networking)
on, so readers can reach it).
That is the one thing this can't do for you.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/TheFactor1/bookbridge-server/main/install.sh | sh
```

It asks two questions (keep a library in sync? use Anna's Archive?) and an
optional Hardcover key, then does the rest:

- installs Docker if it's missing (Linux, with your OK; on a Mac, install
  [Docker Desktop](https://www.docker.com/products/docker-desktop/) first),
- puts the files in `~/bookbridge-server` and starts everything,
- makes **one password** for everything and sets up Shelfmark and
  Calibre-Web with it -- no first-run screens, no `admin/admin123`,
- prints that password and how to connect a reader.

At the end you see something like:

```
 Done. Your server password:  k7mq-r2xp-w9hd

 Connect a reader:
   1. On the reader: Bookbridge > Connect a book server
   2. It shows a code. On your phone or computer open
        http://192.168.1.20:8086
      and enter the code and the password above.
```

## Connect a reader

1. On the reader, tap **Connect a book server**. It finds the server on your
   Wi-Fi by itself and shows a 6-character code.
2. On your phone or computer, open the address the install printed, and type
   the code and the password.
3. The reader gets every address and login in one go. Nothing to type on the
   reader.

Keys and logins you do have to enter on the reader (a Hardcover key, say)
have a **Type on your phone** button: scan the code, paste on the phone.

## Away from home

At home the reader finds the server on its own. To reach it from anywhere,
install [Tailscale](https://tailscale.com/download) on the server and, on a
Kindle or Kobo, the Tailscale KOReader plugin. Then choose **Connect a book
server** and type the server's Tailscale address (the install prints it if
Tailscale is running). Private, no domain, nothing opened to the internet.

## Change, update, remove

Everything lives in `~/bookbridge-server`; the passwords are in its `.env`.

- **Update:** run the install command again. It keeps your settings and
  passwords.
- **Turn a feature on or off:** edit `COMPOSE_PROFILES` in `.env` (see the
  table), then run the install command again.
- **Stop it:** `cd ~/bookbridge-server && docker compose down`
  (your books and settings stay; `up -d` brings it back). Just installed
  Docker? Until you log in again, put `sudo` in front.
- **Set up with the old setup wizard?** Run the install command with your
  folder: `curl -fsSL .../install.sh | BOOKBRIDGE_DIR=/path/to/bookbridge-server sh`.
  It keeps what you have and adds the server password and connecting by code.

| Profile    | Adds                          | What the reader gains                                  | Needs                                             |
|------------|-------------------------------|--------------------------------------------------------|---------------------------------------------------|
| `sync`     | Calibre-Web-NextGen           | Library sync; tap-to-download of delivered books       | Nothing; set `CALIBRE_LIBRARY` in `.env` to use a library you already have |
| `annas`    | annas-archive-api             | Anna's Archive as the primary search source            | An Anna's Archive account key, entered in the plugin |
| `ai-local` | shelfmark-ai-relay + ollama   | Match suggestions after a sync, using a local model    | `RELAY_TOKEN`; ~8 GB free RAM; a ~5 GB model download |
| `ai-cloud` | shelfmark-ai-relay            | Same, using an API key instead of a local model        | `RELAY_TOKEN`, `AI_PROVIDER`, `AI_API_KEY`        |

The install script takes a few settings up front too, as environment
variables before `sh`: `BOOKBRIDGE_DIR`, `BB_FEATURES` (e.g. `sync,annas`),
`BB_HARDCOVER`, and the ports below. They are listed at the top of
[`install.sh`](install.sh).

## How it fits together

Request a book on the reader → Shelfmark fetches it → drops it in the shared
`ingest` volume → Calibre-Web imports it → it shows up in the library the
reader syncs from. That shared volume is why sync works at all; keep it one
volume rather than two host folders.

**shelfmark-pairing-relay** (tiny, always on) connects readers (the code
above), copies settings from one reader to another by QR code, and receives
*Send debug log to server* logs (in its `pairing_logs` volume, never served
back).

Calibre-Web-NextGen is a maintained fork of Calibre-Web-Automated (same
volumes and ports); the upstream stopped releasing after v4.0.6 with security
fixes left unreleased.

## Ports

| Service                 | Host port (default) |
|-------------------------|---------------------|
| shelfmark               | 8084                |
| cwa                     | 8083                |
| annas-archive-api       | 8087                |
| shelfmark-ai-relay      | 8089                |
| shelfmark-pairing-relay | 8086                |

Taken already? Choose another the first time --
`curl -fsSL .../install.sh | SHELFMARK_PORT=9084 sh` (and so on) -- or change
it in `.env` later and run the install again.

## By hand

The install script is a convenience, not a requirement:

```bash
git clone https://github.com/TheFactor1/bookbridge-server && cd bookbridge-server
cp .env.example .env      # fill in what applies
docker compose up -d
```

Then finish Shelfmark's own first-run setup at `http://<this machine>:8084`
(make a local user and set Authentication to Local -- Bookbridge needs a
login), change Calibre-Web's `admin/admin123`, and set `BB_PASSWORD` plus
the logins in `.env` if you want readers to connect with a code.

## Already run TrueNAS or Unraid?

Shelfmark and Calibre-Web-Automated are already in both app catalogs
([TrueNAS](https://apps.truenas.com/catalog/shelfmark_community/),
[Unraid Community Applications](https://ca.unraid.net/apps/shelfmark-1euqv2n0b1eye3)).
Install them from there, and:

- give both the **same** downloads folder (Shelfmark's download folder is
  Calibre-Web's ingest folder), so requested books land in the library;
- in Shelfmark, make a local user and set Authentication to Local (Bookbridge
  needs a login);
- on the reader, enter both addresses and logins under **Bookbridge >
  Settings > Connections**.

Connecting with a code needs this repository's pairing relay as well; with
the two apps on the same machine, set `BB_PASSWORD` and the logins and ports
in `.env` here and run `docker compose up -d shelfmark-pairing-relay`.

## Status

Three of the images here -- `annas-archive-api`, `shelfmark-ai-relay` and
`shelfmark-pairing-relay` -- are published (for amd64 and arm64) by this repository's own workflow
(`.github/workflows/publish-images.yml`, run by hand). Until a run has
published them, they build from the folders beside this file, so a fresh
install always works.

## Credits

This stack only arranges other people's software:
[Shelfmark](https://github.com/calibrain/shelfmark) by calibrain,
[Calibre-Web-NextGen](https://github.com/new-usemame/Calibre-Web-NextGen) by
new-usemame, a fork of
[Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated)
by crocodilestick, [KOReader](https://github.com/koreader/koreader), and
[Anna's Archive](https://annas-archive.org) (searched by the `annas-archive-api`
service beside this file). The compose file, the install script, the relay services and this
document are the only original parts.

## License

[AGPL-3.0](LICENSE) for everything written here. Not covered: the services it
runs, which keep their own licenses, and `annas-archive-api/`, a copy of
[bitesized/annas-archive-api](https://github.com/bitesized/annas-archive-api)
(see its `VENDORED.md`), whose upstream has not published a license.

## Authorship

**Everything in this directory was written by an AI** (Claude, by Anthropic),
with Matt directing, running it on his own server and devices, and deciding
what to keep. Read it before you rely on it.
