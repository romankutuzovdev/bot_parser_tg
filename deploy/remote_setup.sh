#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/tg-parser

apt-get update -y
apt-get install -y python3 python3-venv python3-pip sqlite3

mkdir -p "$APP_DIR"
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# совместимость session-файла со старым Telethon (убрать лишнюю колонку, если есть)
if [[ -f 959767886550_telethon.session ]]; then
  sqlite3 959767886550_telethon.session <<'SQL' || true
BEGIN;
CREATE TABLE IF NOT EXISTS sessions_new (
  dc_id integer primary key,
  server_address text,
  port integer,
  auth_key blob,
  takeout_id integer
);
INSERT OR IGNORE INTO sessions_new (dc_id, server_address, port, auth_key, takeout_id)
SELECT dc_id, server_address, port, auth_key, takeout_id FROM sessions;
DROP TABLE IF EXISTS sessions;
ALTER TABLE sessions_new RENAME TO sessions;
COMMIT;
SQL
fi

install -m 644 /opt/tg-parser/deploy/tg-parser.service /etc/systemd/system/tg-parser.service
systemctl daemon-reload
systemctl enable tg-parser
systemctl restart tg-parser
systemctl --no-pager --full status tg-parser || true
journalctl -u tg-parser -n 40 --no-pager || true
