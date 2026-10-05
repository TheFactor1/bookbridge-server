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
#   BB_PROJECT       docker compose project name  (default shelfmark, or
#                    bookbridge when another "shelfmark" project exists)
#   BB_SOURCE        a local copy to install from instead of downloading
#   SHELFMARK_PORT CWA_PORT PAIRING_PORT ANNAS_API_PORT AI_RELAY_PORT   ports
set -eu

say() { printf '%s\n' "$*"; }
die() { printf '\nStopped: %s\n' "$*" >&2; exit 1; }

DIR=${BOOKBRIDGE_DIR:-$HOME/bookbridge-server}
REPO=https://github.com/TheFactor1/bookbridge-server
# A terminal to ask on, even when the script itself arrives on stdin. (Over
# ssh without -t, in CI or cron there is none: the defaults are used.)
TTY=no
if (: < /dev/tty) 2>/dev/null; then TTY=yes; fi
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
# Missing on Linux: offer Docker's own install script. Can't be used by this
# user yet (just installed, or not in the docker group): use sudo for this
# run and add the user to the group for next time.
DOCKER=docker
OS=$(uname -s)
if ! command -v docker >/dev/null 2>&1; then
    if [ "$OS" = Linux ] && command -v curl >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
        yn=$(ask "Docker isn't installed. Install it now with Docker's own script (get.docker.com, needs sudo)? y/n" "y")
        case "$yn" in [Yy]*) ;; *) die "Install Docker (https://docs.docker.com/get-docker/) and run this again." ;; esac
        curl -fsSL https://get.docker.com | sudo sh || die "Docker's installer didn't finish (output above)."
        sudo usermod -aG docker "$(id -un)" 2>/dev/null || true
    elif [ "$OS" = Darwin ]; then
        die "Install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it once, and run this again."
    else
        die "Docker isn't installed. Get it from https://docs.docker.com/get-docker/ and run this again."
    fi
fi
if ! docker info >/dev/null 2>&1; then
    if [ "$OS" = Linux ] && command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
        sudo usermod -aG docker "$(id -un)" 2>/dev/null || true
    elif [ "$OS" = Linux ] && command -v systemctl >/dev/null 2>&1 && sudo systemctl start docker 2>/dev/null && sudo docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
    else
        die "Docker isn't running. Start it (Docker Desktop, or: sudo systemctl start docker) and run this again."
    fi
fi
$DOCKER compose version >/dev/null 2>&1 || die "Docker is here but 'docker compose' isn't. Install the Compose plugin: https://docs.docker.com/compose/install/"

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
umask 077   # (.env holds every password)
rand() { LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c "$1"; }
setenv() {  # setenv KEY VALUE: set in .env, replacing an existing line
    if grep -q "^$1=" .env 2>/dev/null; then
        tmp=$(mktemp); grep -v "^$1=" .env > "$tmp"; cat "$tmp" > .env; rm -f "$tmp"
    fi
    printf '%s=%s\n' "$1" "$2" >> .env
}
getenv() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -1; }
setdefault() {  # setdefault KEY VALUE: only when the key is empty or missing
    [ -n "$(getenv "$1")" ] || setenv "$1" "$2"
}

# Fresh means no server password yet: no .env, one made by hand from
# .env.example, or a first run that stopped half way. Anything already set
# in .env is kept.
FRESH=no
[ -n "$(getenv BB_PASSWORD)" ] || FRESH=yes
if [ "$FRESH" = yes ]; then
    features=${BB_FEATURES-}
    if [ -z "${BB_FEATURES+x}" ] && [ -z "$(getenv COMPOSE_PROFILES)" ]; then
        sync=$(ask "Keep your library in sync with the reader (Calibre-Web)? y/n" "y")
        annas=$(ask "Use Anna's Archive as a source (needs your own account key)? y/n" "n")
        features=""
        case "$sync" in [Yy]*) features="sync" ;; esac
        case "$annas" in [Yy]*) features="${features:+$features,}annas" ;; esac
    fi
    hardcover=${BB_HARDCOVER-}
    [ -z "${BB_HARDCOVER+x}" ] && [ -z "$(getenv HARDCOVER_TOKEN)" ] && hardcover=$(ask "Hardcover API key (hardcover.app/account/api), or Enter to skip" "")
    # (only now, with every answer in: a stop above leaves nothing half done)
    [ -f .env ] || { [ -f .env.example ] && cp .env.example .env || : > .env; }
    password=$(rand 4)-$(rand 4)-$(rand 4)
    [ -n "$features" ] && setdefault COMPOSE_PROFILES "$features"
    setdefault PUID "$(id -u)"
    setdefault PGID "$(id -g)"
    tz=$(cat /etc/timezone 2>/dev/null || readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || echo UTC)
    setdefault TZ "${tz:-UTC}"
    [ -n "$hardcover" ] && setdefault HARDCOVER_TOKEN "$hardcover"
    setdefault SERVER_NAME "$(hostname 2>/dev/null || echo bookbridge)"
    setenv BB_PASSWORD "$password"
    setdefault SHELFMARK_USERNAME "reader"
    setdefault SHELFMARK_PASSWORD "$password"
    # Calibre-Web gets the same login (set below, in place of admin/admin123)
    [ "$(getenv CWA_PASSWORD)" = admin123 ] && setenv CWA_PASSWORD "" && setenv CWA_USERNAME ""
    setdefault CWA_USERNAME "reader"
    setdefault CWA_PASSWORD "$password"
    case ",$(getenv COMPOSE_PROFILES)," in *,sync,*) setdefault CALIBRE_LIBRARY "$DIR/calibre-library" ;; esac
else
    say "Keeping your settings in $DIR/.env"
fi
chmod 600 .env
for p in SHELFMARK_PORT CWA_PORT PAIRING_PORT ANNAS_API_PORT AI_RELAY_PORT; do
    eval "v=\${$p:-}"; [ -n "$v" ] && setenv "$p" "$v"
done
# the AI relay refuses everything without a token: make one when it's on
case ",$(getenv COMPOSE_PROFILES)," in *,ai-local,*|*,ai-cloud,*) setdefault RELAY_TOKEN "$(rand 32)" ;; esac

# The compose project: kept in .env, so plain `docker compose ...` in this
# folder finds it. "shelfmark" unless another "shelfmark" (an existing
# Shelfmark install, say) lives somewhere else -- never take that one over.
PROJECT=${BB_PROJECT:-$(getenv COMPOSE_PROJECT_NAME)}
if [ -z "$PROJECT" ]; then
    PROJECT=shelfmark
    other=$($DOCKER compose ls -a --filter name=shelfmark --format json 2>/dev/null | tr '{' '\n' | grep '"Name":"shelfmark"' || true)
    if [ -n "$other" ] && ! printf '%s' "$other" | grep -q "$DIR/"; then PROJECT=bookbridge; fi
fi
setenv COMPOSE_PROJECT_NAME "$PROJECT"

# ---- start ---------------------------------------------------------------------
say "Starting (the first time downloads a few hundred MB)..."
compose() { $DOCKER compose -p "$PROJECT" "$@"; }
# images that aren't published yet are built from the folders next to the file
compose pull -q --ignore-buildable >/dev/null 2>&1 || compose pull -q >/dev/null 2>&1 || true
compose up -d --build >/dev/null 2>&1 || compose up -d --build || die "docker compose couldn't start everything (output above)."

# ---- set up Shelfmark ---------------------------------------------------------
say "Setting up Shelfmark..."
i=0
until compose exec -T shelfmark test -f /config/users.db 2>/dev/null; do
    i=$((i + 1)); [ $i -gt 60 ] && die "Shelfmark didn't start within two minutes ('$DOCKER compose -p $PROJECT logs shelfmark' says why)."
    sleep 2
done
compose exec -T -e BB_USER="$(getenv SHELFMARK_USERNAME)" -e BB_PASS="$(getenv SHELFMARK_PASSWORD)" \
    -e BB_HARDCOVER="$(getenv HARDCOVER_TOKEN)" -e BB_FRESH="$FRESH" \
    shelfmark python3 - < setup/seed_shelfmark.py > .seed.log 2>&1 \
    || { cat .seed.log; die "Couldn't set up Shelfmark."; }
grep -v ' - INFO - ' .seed.log | sed 's/^/  /'; rm -f .seed.log
compose restart shelfmark >/dev/null 2>&1

# ---- set up Calibre-Web (library sync) ------------------------------------------
cwa_on=no
case ",$(getenv COMPOSE_PROFILES)," in *,sync,*) cwa_on=yes ;; esac
if [ "$cwa_on" = yes ] && [ -n "$(getenv CWA_PASSWORD)" ]; then
    say "Setting up Calibre-Web..."
    i=0
    # (its first start makes the library and the admin account)
    until compose exec -T cwa sqlite3 /config/app.db "SELECT 1 FROM user WHERE role & 1 LIMIT 1" 2>/dev/null | grep -q 1; do
        i=$((i + 1)); [ $i -gt 90 ] && die "Calibre-Web didn't start within three minutes ('$DOCKER compose -p $PROJECT logs cwa' says why)."
        sleep 2
    done
    out=$(compose exec -T -e BB_USER="$(getenv CWA_USERNAME)" -e BB_PASS="$(getenv CWA_PASSWORD)" \
        cwa python3 - < setup/seed_cwa.py 2>&1) || { say "$out"; die "Couldn't set up Calibre-Web."; }
    say "  $out"
fi

# ---- tell the person what to do ------------------------------------------------
lan=$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
[ -n "$lan" ] || lan=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$lan" ] || lan=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
ts=$(tailscale ip -4 2>/dev/null | head -1 || true)
pair_port=$(getenv PAIRING_PORT); pair_port=${pair_port:-8086}
sm_port=$(getenv SHELFMARK_PORT); sm_port=${sm_port:-8084}

say ""
say "========================================================"
say " Done. Your server password:  $(getenv BB_PASSWORD)"
say "========================================================"
say ""
say " Connect a reader:"
say "   1. On the reader: Bookbridge > Connect a book server"
say "   2. It shows a code. On your phone or computer open"
say "        http://${lan:-<this machine>}:$pair_port"
say "      and enter the code and the password above."
[ -n "$ts" ] && say "   (Away from home on Tailscale? Type $ts on the reader.)"
say ""
say " In a browser (log in as $(getenv SHELFMARK_USERNAME), same password):"
say "   Shelfmark      http://${lan:-<this machine>}:$sm_port"
cwa_port=$(getenv CWA_PORT); cwa_port=${cwa_port:-8083}
[ "$cwa_on" = yes ] && say "   Calibre-Web    http://${lan:-<this machine>}:$cwa_port"
say ""
say " Everything is in $DIR (passwords in .env). Run this again to update."
case "$(uname -r 2>/dev/null)" in *[Mm]icrosoft*)
    say ""
    say " Windows (WSL): readers reach this only with WSL's mirrored networking"
    say " on (https://learn.microsoft.com/windows/wsl/networking#mirrored-mode-networking)."
    say " Then the address above is your PC's; check it with ipconfig in Windows." ;;
esac
