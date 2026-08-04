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

for f in install.sh backup.sh wgstats.sh htmlgen.py config cron/awgstat VERSION; do
    fix_crlf "${SRC}/${f}"
done

echo "Installing AWGStat ${VERSION} → ${DEST}"

mkdir -p "${DEST}/logs" "${DEST}/www" "${DEST}/backups"

# Application files (always refresh)
install -m 0755 "${SRC}/wgstats.sh" "${DEST}/wgstats.sh"
install -m 0755 "${SRC}/htmlgen.py" "${DEST}/htmlgen.py"
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
fi

# Local data — create only if absent, never overwrite
if [[ ! -f "${DEST}/names.map" ]]; then
    install -m 0644 "${SRC}/names.map.example" "${DEST}/names.map"
fi
# history.csv / last.db / .last_backup / backups/* — owned by runtime, never touched

WEBROOT="$(awk -F= '/^WEBROOT=/{gsub(/"/,"",$2); print $2; exit}' "${DEST}/config" || true)"
WEBROOT="${WEBROOT:-$WEBROOT_DEFAULT}"
mkdir -p "${WEBROOT}"
install -m 0644 "${SRC}/style.css" "${WEBROOT}/style.css"

install -m 0644 "${SRC}/cron/awgstat" "${CRON_DST}"

# Force HTML rebuild after upgrade
touch "${DEST}/.changed"
python3 "${DEST}/htmlgen.py" || true

echo "Done. AWGStat ${VERSION}"
echo "  code:    ${DEST}"
echo "  webroot: ${WEBROOT}"
echo "  cron:    ${CRON_DST}"
echo "  preserved: names.map history.csv last.db backups/ config(local keys)"
