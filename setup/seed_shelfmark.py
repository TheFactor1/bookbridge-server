"""Set up a fresh Shelfmark so nobody has to click through its first-run
wizard. Run INSIDE the shelfmark container by install.sh:

    docker compose exec -T -e BB_USER=... -e BB_PASS=... [-e BB_HARDCOVER=...] \
        shelfmark python3 - < setup/seed_shelfmark.py

Uses Shelfmark's own code (settings files, user database, password hashing),
so the result is exactly what its wizard and Settings would have made, and
every value stays changeable in Shelfmark's Settings afterwards. Safe to run
again: settings are merged, and an existing account is left alone.
"""
import os
import sys

sys.path.insert(0, "/app")
from werkzeug.security import generate_password_hash  # noqa: E402

from shelfmark.core.settings_registry import save_config_file  # noqa: E402
from shelfmark.core.user_db import UserDB, get_users_db_path  # noqa: E402

user = os.environ.get("BB_USER", "").strip()
password = os.environ.get("BB_PASS", "")
hardcover = os.environ.get("BB_HARDCOVER", "").strip()
if not user or not password:
    sys.exit("BB_USER and BB_PASS are required")

# search: Hardcover when there is a key, otherwise Open Library (no key needed)
general = {"onboarding_complete": True}
if hardcover:
    general["METADATA_PROVIDER"] = "hardcover"
    save_config_file("hardcover", {"HARDCOVER_ENABLED": True, "HARDCOVER_API_KEY": hardcover})
else:
    general["METADATA_PROVIDER"] = "openlibrary"
    save_config_file("openlibrary", {"OPENLIBRARY_ENABLED": True})
save_config_file("general", general)

# logins: Shelfmark's own accounts ("Local"); it stays off until an admin exists
db = UserDB(get_users_db_path())
db.initialize() if hasattr(db, "initialize") else None
existing = db.get_user(username=user)
if existing:
    print("account %s already exists; left as it is" % user)
else:
    db.create_user(username=user, password_hash=generate_password_hash(password), role="admin")
    print("made admin account %s" % user)
save_config_file("security", {"AUTH_METHOD": "builtin"})
print("shelfmark set up: search via %s, Local logins" % general["METADATA_PROVIDER"])
