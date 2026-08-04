#!/usr/bin/env bash
# Install or upgrade AWGStat on this host.
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

echo "Installing AWGStat ${VERSION} → ${DEST}"

mkdir -p "${DEST}/logs" "${DEST}/www"
install -m 0755 "${SRC}/wgstats.sh" "${DEST}/wgstats.sh"
install -m 0755 "${SRC}/htmlgen.py" "${DEST}/htmlgen.py"
install -m 0644 "${SRC}/style.css" "${DEST}/style.css"
install -m 0644 "${SRC}/VERSION" "${DEST}/VERSION"

if [[ ! -f "${DEST}/config" ]]; then
    install -m 0644 "${SRC}/config" "${DEST}/config"
else
    # Refresh VERSION in existing config; keep other settings.
    if grep -q '^VERSION=' "${DEST}/config"; then
        sed -i "s/^VERSION=.*/VERSION=\"${VERSION}\"/" "${DEST}/config"
    else
        printf '\nVERSION="%s"\n' "${VERSION}" >>"${DEST}/config"
    fi
fi

if [[ ! -f "${DEST}/names.map" ]]; then
    install -m 0644 "${SRC}/names.map.example" "${DEST}/names.map"
fi

WEBROOT="$(awk -F= '/^WEBROOT=/{gsub(/"/,"",$2); print $2; exit}' "${DEST}/config" || true)"
WEBROOT="${WEBROOT:-$WEBROOT_DEFAULT}"
mkdir -p "${WEBROOT}"
install -m 0644 "${SRC}/style.css" "${WEBROOT}/style.css"

install -m 0644 "${SRC}/cron/awgstat" "${CRON_DST}"

# Force HTML rebuild on next cron tick
touch "${DEST}/.changed"
python3 "${DEST}/htmlgen.py" || true

echo "Done. AWGStat ${VERSION}"
echo "  code:    ${DEST}"
echo "  webroot: ${WEBROOT}"
echo "  cron:    ${CRON_DST}"
