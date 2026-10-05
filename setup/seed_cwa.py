# Run inside the Calibre-Web container by install.sh:
#   docker compose exec -T -e BB_USER=... -e BB_PASS=... cwa python3 - < setup/seed_cwa.py
#
# Calibre-Web starts with a well-known admin / admin123 login. This gives
# that admin account the installer's name and password instead, so there is
# one login for everything and nothing left to "remember to change". Only
# while it is still the factory login: once someone has changed it, this
# leaves it alone.
import os
import sqlite3
import sys

from werkzeug.security import check_password_hash, generate_password_hash

user, password = os.environ.get("BB_USER", ""), os.environ.get("BB_PASS", "")
if not user or not password:
    sys.exit("BB_USER and BB_PASS are needed")

db = sqlite3.connect("/config/app.db")
row = db.execute("SELECT id, name, password FROM user WHERE role & 1 ORDER BY id LIMIT 1").fetchone()
if not row:
    sys.exit("no admin account in Calibre-Web yet")
uid, name, pw_hash = row
if name == user and check_password_hash(pw_hash or "", password):
    print("Calibre-Web: login already set")
elif name == "admin" and check_password_hash(pw_hash or "", "admin123"):
    db.execute("UPDATE user SET name = ?, password = ? WHERE id = ?",
               (user, generate_password_hash(password), uid))
    db.commit()
    print("Calibre-Web: login set")
else:
    print("Calibre-Web: login was changed by hand; leaving it. Put that login in .env "
          "(CWA_USERNAME, CWA_PASSWORD) so newly connected readers get it.")
