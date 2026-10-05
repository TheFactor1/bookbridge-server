#!/bin/sh
# Bookbridge server: one command, no wizard.
#
#   curl -fsSL https://raw.githubusercontent.com/TheFactor1/bookbridge-server/main/install.sh | sh
#
# Gets the server files, writes .env with passwords it makes up itself,
# starts everything, sets up Shelfmark (no first-run wizard: Local logins,
# an admin account, Open Library search -- or Hardcover if you give a key),
# and prints the one password you need and how to connect a reader.
#
# Run it again any time: it keeps your .env and passwords, picks up new
# files and restarts what changed.
#
# Optional, as environment variables in front of `sh`:
#   BOOKBRIDGE_DIR   where to put it              (default ~/bookbridge-server)
#   BB_FEATURES      sync,annas                   (default: ask; sync if no terminal)
#   BB_HARDCOVER     a Hardcover API key          (default: ask; none)
#   BB_PROJECT       docker compose project name  (default shelfmark)
#   BB_SOURCE        a local copy to install from instead of downloading
#   SHELFMARK_PORT CWA_PORT PAIRING_PORT ANNAS_API_PORT   other ports
set -eu

say() { printf '%s\n' "$*"; }
die() { printf '\nStopped: %s\n' "$*" >&2; exit 1; }

DIR=${BOOKBRIDGE_DIR:-$HOME/bookbridge-server}
PROJECT=${BB_PROJECT:-shelfmark}
REPO=https://github.com/TheFactor1/bookbridge-server
TTY=no; [ -t 0 ] || [ -r /dev/tty ] && TTY=yes
ask() {  # ask "question" default -> answer (from the terminal even when piped)
    if [ "$TTY" = yes ] && [ -r /dev/tty ]; then
        printf '%s [%s] ' "$1" "$2" > /dev/tty
        read -r a < /dev/tty || a=""
        [ -n "$a" ] && { printf '%s' "$a"; return; }
    fi
    printf '%s' "$2"
}

say "Bookbridge server"
say "-----------------"

# ---- Docker -----------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker isn't installed. Get it from https://docs.docker.com/get-docker/ and run this again."
docker compose version >/dev/null 2>&1 || die "Docker is here but 'docker compose' isn't. Install the Compose plugin: https://docs.docker.com/compose/install/"
docker info >/dev/null 2>&1 || die "Docker isn't running, or this user can't use it. Start Docker (or add yourself to the docker group: sudo usermod -aG docker \$USER, then log in again)."

# ---- the files ----------------------------------------------------------------
mkdir -p "$DIR"
if [ -n "${BB_SOURCE:-}" ]; then
    say "Copying the server files from $BB_SOURCE"
    (cd "$BB_SOURCE" && tar cf - --exclude=.git --exclude=.env --exclude='__pycache__' .) | (cd "$DIR" && tar xf -)
elif [ -d "$DIR/.git" ] && command -v git >/dev/null 2>&1; then
    say "Updating the server files in $DIR"
    git -C "$DIR" pull --ff-only -q || say "(couldn't update; using what's there)"
elif command -v git >/dev/null 2>&1 && [ ! -f "$DIR/docker-compose.yml" ]; then
    say "Getting the server files into $DIR"
    git clone -q "$REPO" "$DIR"
else
    say "Getting the server files into $DIR"
    command -v curl >/dev/null 2>&1 || die "Needs curl (or git) to download the files."
    curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar xz --strip-components=1 -C "$DIR"
fi
cd "$DIR"
[ -f docker-compose.yml ] || die "The download didn't give a docker-compose.yml in $DIR."

# ---- .env: kept if it's there, written if not ----------------------------------
rand() { LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c "$1"; }
setenv() {  # setenv KEY VALUE: set in .env, replacing an existing line
    if grep -q "^$1=" .env 2>/dev/null; then
        tmp=$(mktemp); grep -v "^$1=" .env > "$tmp"; cat "$tmp" > .env; rm -f "$tmp"
    fi
    printf '%s=%s\n' "$1" "$2" >> .env
}
getenv() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -1; }

if [ ! -f .env ]; then
    [ -f .env.example ] && cp .env.example .env || : > .env
    FRESH=yes
else
    FRESH=no
    say "Keeping your settings in $DIR/.env"
fi

if [ "$FRESH" = yes ]; then
    features=${BB_FEATURES:-}
    if [ -z "$features" ]; then
        sync=$(ask "Keep your library in sync with the reader (Calibre-Web)? y/n" "y")
        annas=$(ask "Use Anna's Archive as a source (needs your own account key)? y/n" "n")
        features=""
        case "$sync" in [Yy]*) features="sync" ;; esac
        case "$annas" in [Yy]*) features="${features:+$features,}annas" ;; esac
    fi
    hardcover=${BB_HARDCOVER-}
    [ -z "${BB_HARDCOVER+x}" ] && hardcover=$(ask "Hardcover API key for better search (from hardcover.app/account/api; Enter to skip)" "")
    password=$(rand 4)-$(rand 4)-$(rand 4)
    setenv COMPOSE_PROFILES "$features"
    setenv PUID "$(id -u)"
    setenv PGID "$(id -g)"
    tz=$(cat /etc/timezone 2>/dev/null || readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || echo UTC)
    setenv TZ "${tz:-UTC}"
    setenv HARDCOVER_TOKEN "$hardcover"
    setenv SERVER_NAME "$(hostname 2>/dev/null || echo bookbridge)"
    setenv BB_PASSWORD "$password"
    setenv SHELFMARK_USERNAME "reader"
    setenv SHELFMARK_PASSWORD "$password"
    case ",$features," in *,sync,*) setenv CALIBRE_LIBRARY "$DIR/calibre-library" ;; esac
fi
for p in SHELFMARK_PORT CWA_PORT PAIRING_PORT ANNAS_API_PORT; do
    eval "v=\${$p:-}"; [ -n "$v" ] && setenv "$p" "$v"
done

# ---- start ---------------------------------------------------------------------
say "Starting (the first time downloads a few hundred MB)..."
compose() { docker compose -p "$PROJECT" "$@"; }
# images that aren't published yet are built from the folders next to the file
compose pull -q --ignore-buildable >/dev/null 2>&1 || compose pull -q >/dev/null 2>&1 || true
compose up -d --build >/dev/null 2>&1 || compose up -d --build || die "docker compose couldn't start everything (output above)."

# ---- set up Shelfmark ---------------------------------------------------------
say "Setting up Shelfmark..."
i=0
until compose exec -T shelfmark test -f /config/users.db 2>/dev/null; do
    i=$((i + 1)); [ $i -gt 60 ] && die "Shelfmark didn't start within two minutes ('docker compose -p $PROJECT logs shelfmark' says why)."
    sleep 2
done
compose exec -T -e BB_USER="$(getenv SHELFMARK_USERNAME)" -e BB_PASS="$(getenv SHELFMARK_PASSWORD)" \
    -e BB_HARDCOVER="$(getenv HARDCOVER_TOKEN)" shelfmark python3 - < setup/seed_shelfmark.py \
    || die "Couldn't set up Shelfmark."
compose restart shelfmark >/dev/null 2>&1

# ---- tell the person what to do ------------------------------------------------
lan=$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
[ -n "$lan" ] || lan=$(hostname -I 2>/dev/null | awk '{print $1}')
ts=$(tailscale ip -4 2>/dev/null | head -1 || true)
pair_port=$(getenv PAIRING_PORT); pair_port=${pair_port:-8086}
sm_port=$(getenv SHELFMARK_PORT); sm_port=${sm_port:-8084}

say ""
say "========================================================"
say " Done. Your server password:  $(getenv BB_PASSWORD)"
say "========================================================"
say ""
say " Connect a reader:"
say "   1. On the reader: Reading Ledger > Settings > Bookbridge >"
say "      Connect a book server   (or Bookbridge > Connect a book server)"
say "   2. It shows a code. On your phone or computer open"
say "        http://${lan:-<this machine>}:$pair_port"
say "      and enter the code and the password above."
[ -n "$ts" ] && say "   (Away from home on Tailscale? Type $ts on the reader.)"
say ""
say " Shelfmark in a browser: http://${lan:-<this machine>}:$sm_port"
say "   (log in as $(getenv SHELFMARK_USERNAME) with the same password)"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    say ""
    say " This machine has a firewall (ufw) on. Let readers in with:"
    say "   sudo ufw allow $pair_port,$sm_port,$(getenv CWA_PORT | sed 's/^$/8083/')/tcp"
fi
say ""
say " Everything is in $DIR (passwords in .env). Run this again to update."
