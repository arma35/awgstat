#!/usr/bin/env bash
# AWGStat backup — archives local data if BACKUP_DAYS elapsed since last run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config
source "${SCRIPT_DIR}/config"

LOG_DIR="${WORKDIR}/logs"
LOG_FILE="${LOG_DIR}/wgstats.log"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

mkdir -p "${LOG_DIR}"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "backup: $*" >>"${LOG_FILE}"
}

BACKUP_DAYS="${BACKUP_DAYS:-7}"
BACKUP_DIR="${BACKUP_DIR:-${WORKDIR}/backups}"
LAST_BACKUP="${LAST_BACKUP:-${WORKDIR}/.last_backup}"

if ! [[ "${BACKUP_DAYS}" =~ ^[0-9]+$ ]] || (( BACKUP_DAYS < 1 )); then
    log "ERROR: BACKUP_DAYS must be >= 1 (got '${BACKUP_DAYS}')"
    exit 1
fi

NOW="$(date +%s)"
LAST=0
if [[ -f "${LAST_BACKUP}" ]]; then
    LAST="$(tr -d '[:space:]' <"${LAST_BACKUP}" || true)"
    [[ "${LAST}" =~ ^[0-9]+$ ]] || LAST=0
fi

elapsed=$((NOW - LAST))
need=$((BACKUP_DAYS * 86400))

if (( FORCE == 0 && LAST > 0 && elapsed < need )); then
    # Period not reached — silent success for cron
    exit 0
fi

mkdir -p "${BACKUP_DIR}"

stamp="$(date -d "@${NOW}" '+%Y%m%d-%H%M%S' 2>/dev/null || date -u -d "@${NOW}" '+%Y%m%d-%H%M%S' 2>/dev/null || date '+%Y%m%d-%H%M%S')"
archive="${BACKUP_DIR}/awgstat-data-${stamp}.tar.gz"

# Collect existing data files only
files=()
for f in \
    "${NAMES}" \
    "${HISTORY}" \
    "${LASTDB}" \
    "${SCRIPT_DIR}/config" \
    "${SCRIPT_DIR}/VERSION"
do
    [[ -e "${f}" ]] && files+=("${f}")
done

if (( ${#files[@]} == 0 )); then
    log "ERROR: nothing to back up"
    exit 1
fi

# Store paths relative to WORKDIR / SCRIPT_DIR parents for a clean archive
tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

mkdir -p "${tmpdir}/data"
for f in "${files[@]}"; do
    cp -a "${f}" "${tmpdir}/data/$(basename "${f}")"
done

tar -czf "${archive}" -C "${tmpdir}" data
chmod 600 "${archive}" 2>/dev/null || true

printf '%s\n' "${NOW}" >"${LAST_BACKUP}"
log "created ${archive} (period=${BACKUP_DAYS}d)"
exit 0
