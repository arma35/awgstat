#!/usr/bin/env bash
# Install or upgrade AWGStat on this host.
# Never overwrites local traffic data: history.csv, last.db, backups, logs.
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

fix_crlf() {
    local f="$1"
    [[ -f "${f}" ]] || return 0
    sed -i 's/\r$//' "${f}"
}

for f in install.sh update.sh backup.sh wgstats.sh awgstat-cycle.sh htmlgen.py reportgen.py amnezia_names.py online.js config cron/awgstat VERSION; do
    fix_crlf "${SRC}/${f}"
done

echo "Installing AWGStat ${VERSION} → ${DEST}"

mkdir -p "${DEST}/logs" "${DEST}/www" "${DEST}/backups"

# Application files (always refresh)
install -m 0755 "${SRC}/wgstats.sh" "${DEST}/wgstats.sh"
install -m 0755 "${SRC}/awgstat-cycle.sh" "${DEST}/awgstat-cycle.sh"
install -m 0755 "${SRC}/htmlgen.py" "${DEST}/htmlgen.py"
install -m 0644 "${SRC}/reportgen.py" "${DEST}/reportgen.py"
install -m 0755 "${SRC}/amnezia_names.py" "${DEST}/amnezia_names.py"
install -m 0644 "${SRC}/online.js" "${DEST}/online.js"
install -m 0755 "${SRC}/backup.sh" "${DEST}/backup.sh"
install -m 0755 "${SRC}/update.sh" "${DEST}/update.sh"
install -m 0644 "${SRC}/style.css" "${DEST}/style.css"
install -m 0644 "${SRC}/VERSION" "${DEST}/VERSION"
rm -f "${DEST}/names.map.example"

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
    # Keep local settings, bump VERSION, add new keys, and remove obsolete
    # names.map-era settings. An old names.map file itself is left untouched.
    if grep -q '^VERSION=' "${DEST}/config"; then
        sed -i "s/^VERSION=.*/VERSION=\"${VERSION}\"/" "${DEST}/config"
    else
        printf '\nVERSION="%s"\n' "${VERSION}" >>"${DEST}/config"
    fi
    sed -i '/^NAMES=/d;/^AUTO_DISCOVER_NAMES=/d' "${DEST}/config"
    ensure_config_key "BACKUP_DAYS" "7"
    ensure_config_key "BACKUP_DIR" '"${WORKDIR}/backups"'
    ensure_config_key "LAST_BACKUP" '"${WORKDIR}/.last_backup"'
    ensure_config_key "REPORT_TZ" '"Europe/Moscow"'
    ensure_config_key "DAILY_REPORTS" "31"
    ensure_config_key "WEEKLY_REPORTS" "12"
    ensure_config_key "MONTHLY_REPORTS" "12"
    ensure_config_key "DETAIL_ROWS" "200"
    ensure_config_key "ONLINE_STATE" '"${WORKDIR}/online.csv"'
    ensure_config_key "ONLINE_WINDOW_MINUTES" "60"
    ensure_config_key "ONLINE_ACTIVE_MINUTES" "3"
    ensure_config_key "ONLINE_STALE_MINUTES" "3"
    ensure_config_key "ONLINE_REFRESH_SECONDS" "60"
    ensure_config_key "AMNEZIA_CLIENTS_TABLE" '"/opt/amnezia/awg/clientsTable"'
fi

WEBROOT="$(awk -F= '/^WEBROOT=/{gsub(/"/,"",$2); print $2; exit}' "${DEST}/config" || true)"
WEBROOT="${WEBROOT:-$WEBROOT_DEFAULT}"
mkdir -p "${WEBROOT}"

REPORT_TZ="$(awk -F= '/^REPORT_TZ=/{gsub(/"/,"",$2); print $2; exit}' "${DEST}/config" || true)"
REPORT_TZ="${REPORT_TZ:-Europe/Moscow}"
sed "s|__REPORT_TZ__|${REPORT_TZ}|g" "${SRC}/cron/awgstat" >"${CRON_DST}"
chmod 644 "${CRON_DST}"

# Force HTML rebuild after upgrade. htmlgen reads current names directly from
# Amnezia clientsTable; historical rows retain the last known name for revoked peers.
touch "${DEST}/.changed"
python3 "${DEST}/htmlgen.py" --force

echo "Done. AWGStat ${VERSION}"
echo "  code:    ${DEST}"
echo "  webroot: ${WEBROOT}"
echo "  tz:      ${REPORT_TZ}"
echo "  cron:    ${CRON_DST}  (not visible in crontab -l — use: cat ${CRON_DST})"
echo "  updater: ${DEST}/update.sh"
echo "  names:   live from Amnezia clientsTable; legacy names.map is ignored"
echo "  preserved: history.csv last.db backups/ config(local keys)"
echo "--- cron jobs ---"
cat "${CRON_DST}"
