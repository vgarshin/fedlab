#!/usr/bin/env bash
# Extracts the Postgres password from config/secrets.json into config/.pg_password (mode 600),
# because the postgres image reads a plain-text secret file. Run before the first `docker compose up`.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import json, os, sys
s = json.load(open("config/secrets.json"))
pw = s.get("app", {}).get("postgres_password", "")
if not pw or pw.startswith("PUT_"):
    sys.exit("config/secrets.json: app.postgres_password is empty")
fd = os.open("config/.pg_password", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, pw.encode()); os.close(fd)
print("config/.pg_password written (600)")
PY
chmod 600 config/secrets.json config/.pg_password
