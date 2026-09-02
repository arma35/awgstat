#!/usr/bin/env bash
# Install or upgrade AWGStat on this host.
# Never overwrites local data: names.map, history.csv, last.db, backups, logs.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' <"${SRC}/VERSION")"
DEST="${DEST:-/opt/wgstats}"
WEBROOT_DEFAULT="/var/www/html-20180/wgstats"
CRON_DST="/etc/cron.d/awgstat"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

# Strip CRLF if archive was built/edited on Windows
fix_crlf() {
    local f="$1"
    [[ -f "${f}" ]] || return 0
    sed -i 's/\r$//' "${f}"
}

for f in install.sh backup.sh wgstats.sh htmlgen.py reportgen.py config cron/awgstat VERSION; do
    fix_crlf "${SRC}/${f}"
done

echo "Installing AWGStat ${VERSION} → ${DEST}"

mkdir -p "${DEST}/logs" "${DEST}/www" "${DEST}/backups"

# Application files (always refresh)
install -m 0755 "${SRC}/wgstats.sh" "${DEST}/wgstats.sh"
install -m 0755 "${SRC}/htmlgen.py" "${DEST}/htmlgen.py"
install -m 0644 "${SRC}/reportgen.py" "${DEST}/reportgen.py"
install -m 0755 "${SRC}/backup.sh" "${DEST}/backup.sh"
install -m 0644 "${SRC}/style.css" "${DEST}/style.css"
install -m 0644 "${SRC}/VERSION" "${DEST}/VERSION"
install -m 0644 "${SRC}/names.map.example" "${DEST}/names.map.example"

ensure_config_key() {
    local key="$1"
    local value="$2"
    if ! grep -q "^${key}=" "${DEST}/config"; then
        printf '\n%s=%s\n' "${key}" "${value}" >>"${DEST}/config"
    fi
}

if [[ ! -f "${DEST}/config" ]]; then
    install -m 0644 "${SRC}/config" "${DEST}/config"
else
    # Keep all local settings; only bump VERSION and add new keys if missing
    if grep -q '^VERSION=' "${DEST}/config"; then
        sed -i "s/^VERSION=.*/VERSION=\"${VERSION}\"/" "${DEST}/config"
    else
        printf '\nVERSION="%s"\n' "${VERSION}" >>"${DEST}/config"
    fi
    ensure_config_key "BACKUP_DAYS" "7"
    ensure_config_key "BACKUP_DIR" '"${WORKDIR}/backups"'
    ensure_config_key "LAST_BACKUP" '"${WORKDIR}/.last_backup"'
    ensure_config_key "REPORT_TZ" '"Europe/Moscow"'
    ensure_config_key "DAILY_REPORTS" "31"
    ensure_config_key "WEEKLY_REPORTS" "12"
    ensure_config_key "MONTHLY_REPORTS" "12"
    ensure_config_key "DETAIL_ROWS" "200"
fi

# Local data — create only if absent, never overwrite
if [[ ! -f "${DEST}/names.map" ]]; then
    install -m 0644 "${SRC}/names.map.example" "${DEST}/names.map"
fi

# Repair names.map to canonical pubkey:name (colon) format
NAMES_FILE="${DEST}/names.map"
export NAMES_FILE
python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["NAMES_FILE"])
if not path.exists():
    raise SystemExit(0)

names = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    if ":" in line:
        key, val = line.split(":", 1)
    elif "=" in line:
        key, val = line.rsplit("=", 1)
    else:
        continue
    key, val = key.strip(), val.strip()
    if val.startswith(":"):
        val = val[1:]
    if not key or not val:
        continue
    prev = names.get(key)
    if prev is None:
        names[key] = val
    elif prev.startswith("неизвестный") and not val.startswith("неизвестный"):
        names[key] = val

lines = [f"{k}:{v}\n" for k, v in sorted(names.items())]
new = "".join(lines)
old = path.read_text(encoding="utf-8")
if new != old:
    path.write_text(new, encoding="utf-8")
    print(f"names.map repaired → {len(names)} peers (colon format)")
else:
    print(f"names.map ok → {len(names)} peers")
PY

WEBROOT="$(awk -F= '/^WEBROOT=/{gsub(/"/,"",$2); print $2; exit}' "${DEST}/config" || true)"
WEBROOT="${WEBROOT:-$WEBROOT_DEFAULT}"
mkdir -p "${WEBROOT}"
install -m 0644 "${SRC}/style.css" "${WEBROOT}/style.css"

REPORT_TZ="$(awk -F= '/^REPORT_TZ=/{gsub(/"/,"",$2); print $2; exit}' "${DEST}/config" || true)"
REPORT_TZ="${REPORT_TZ:-Europe/Moscow}"
sed "s|__REPORT_TZ__|${REPORT_TZ}|g" "${SRC}/cron/awgstat" >"${CRON_DST}"
chmod 644 "${CRON_DST}"

# Force HTML rebuild after upgrade
touch "${DEST}/.changed"
python3 "${DEST}/htmlgen.py" --force || true

echo "Done. AWGStat ${VERSION}"
echo "  code:    ${DEST}"
echo "  webroot: ${WEBROOT}"
echo "  tz:      ${REPORT_TZ}"
echo "  cron:    ${CRON_DST}  (not visible in crontab -l — use: cat ${CRON_DST})"
echo "  preserved: names.map history.csv last.db backups/ config(local keys)"
echo "--- cron jobs ---"
cat "${CRON_DST}"
echo "--- names.map ---"
cat "${DEST}/names.map"
