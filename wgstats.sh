#!/usr/bin/env bash
# AWGStat collector — records per-peer traffic deltas from AmneziaWG
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config
source "${SCRIPT_DIR}/config"

if [[ -z "${VERSION:-}" && -f "${SCRIPT_DIR}/VERSION" ]]; then
    VERSION="$(tr -d '[:space:]' <"${SCRIPT_DIR}/VERSION")"
fi
VERSION="${VERSION:-0.0.0}"

LOG_DIR="${WORKDIR}/logs"
LOG_FILE="${LOG_DIR}/wgstats.log"
LOCK_FILE="${WORKDIR}/.lock"
CSV_VERSION="#WGSTAT:1"
CSV_HEADER="timestamp;date;time;peer;name;ip;rx_bytes;tx_bytes;interval;handshake"

mkdir -p "${LOG_DIR}"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"
}

die() {
    log "ERROR: $*"
    exit 1
}

exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

load_names() {
    declare -gA PEER_NAMES=()
    [[ -f "${NAMES}" ]] || return 0
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line="${line%%#*}"
        line="$(echo "${line}" | xargs)"
        [[ -z "${line}" ]] && continue
        if [[ "${line}" == *"="* ]]; then
            key="${line%%=*}"
            val="${line#*=}"
            key="$(echo "${key}" | xargs)"
            val="$(echo "${val}" | xargs)"
            PEER_NAMES["${key}"]="${val}"
        fi
    done <"${NAMES}"
}

peer_name() {
    local peer="$1"
    if [[ -n "${PEER_NAMES[${peer}]:-}" ]]; then
        echo "${PEER_NAMES[${peer}]}"
    else
        echo ""
    fi
}

init_history() {
    if [[ ! -f "${HISTORY}" ]]; then
        {
            echo "${CSV_VERSION}"
            echo "${CSV_HEADER}"
        } >"${HISTORY}"
    elif ! grep -q '^#WGSTAT:' "${HISTORY}" 2>/dev/null; then
        local tmp
        tmp="$(mktemp)"
        {
            echo "${CSV_VERSION}"
            echo "${CSV_HEADER}"
            grep -v '^#' "${HISTORY}" || true
        } >"${tmp}"
        mv "${tmp}" "${HISTORY}"
    fi
}

read_lastdb() {
    declare -gA LAST_RX=()
    declare -gA LAST_TX=()
    declare -gA LAST_TS=()
    [[ -f "${LASTDB}" ]] || return 0
    while IFS=';' read -r peer rx tx ts _rest; do
        [[ -z "${peer}" || "${peer}" == peer ]] && continue
        LAST_RX["${peer}"]="${rx:-0}"
        LAST_TX["${peer}"]="${tx:-0}"
        LAST_TS["${peer}"]="${ts:-0}"
    done <"${LASTDB}"
}

calc_delta() {
    local current="$1"
    local last="$2"
    if (( current >= last )); then
        echo $((current - last))
    else
        # Counter reset after container restart
        echo "${current}"
    fi
}

write_lastdb() {
    local tmp
    tmp="$(mktemp)"
    {
        echo "peer;rx;tx;ts"
        for peer in "${!CURRENT_RX[@]}"; do
            echo "${peer};${CURRENT_RX[${peer}]};${CURRENT_TX[${peer}]};${NOW}"
        done
    } >"${tmp}"
    mv "${tmp}" "${LASTDB}"
}

prune_history() {
    [[ "${RETENTION_DAYS}" -gt 0 ]] || return 0
    local cutoff tmp
    cutoff=$((NOW - RETENTION_DAYS * 86400))
    tmp="$(mktemp)"
    {
        head -n 2 "${HISTORY}"
        awk -F';' -v c="${cutoff}" 'NR>2 && $1+0 >= c {print}' "${HISTORY}"
    } >"${tmp}"
    mv "${tmp}" "${HISTORY}"
}

get_dump() {
    docker exec "${CONTAINER}" wg show "${WG_INTERFACE}" dump 2>/dev/null \
        || die "cannot read wg dump from container ${CONTAINER}"
}

load_names
init_history
read_lastdb

NOW="$(date +%s)"
DATE="$(date '+%Y-%m-%d')"
TIME="$(date '+%H:%M:%S')"

declare -A CURRENT_RX=()
declare -A CURRENT_TX=()
declare -A CURRENT_IP=()
declare -A CURRENT_HS=()

mapfile -t DUMP_LINES < <(get_dump)
(( ${#DUMP_LINES[@]} > 0 )) || die "empty wg dump"

changed=0
tmp_history="$(mktemp)"

while IFS=$'\t' read -r peer _psk _endpoint allowed_ips handshake rx tx _keepalive; do
    [[ -z "${peer}" || "${peer}" == "public-key" ]] && continue

    rx="${rx:-0}"
    tx="${tx:-0}"
    handshake="${handshake:-0}"

    CURRENT_RX["${peer}"]="${rx}"
    CURRENT_TX["${peer}"]="${tx}"
    CURRENT_IP["${peer}"]="${allowed_ips:-}"
    CURRENT_HS["${peer}"]="${handshake}"

    if [[ -z "${LAST_RX[${peer}]:-}" ]]; then
        continue
    fi

    drx="$(calc_delta "${rx}" "${LAST_RX[${peer}]}")"
    dtx="$(calc_delta "${tx}" "${LAST_TX[${peer}]}")"
    interval=$((NOW - LAST_TS[${peer}]))
    (( interval < 0 )) && interval=0

    if (( drx == 0 && dtx == 0 )); then
        continue
    fi

    name="$(peer_name "${peer}")"
    printf '%s;%s;%s;%s;%s;%s;%s;%s;%s;%s\n' \
        "${NOW}" "${DATE}" "${TIME}" "${peer}" "${name}" \
        "${allowed_ips:-}" "${drx}" "${dtx}" "${interval}" "${handshake}" >>"${tmp_history}"
    changed=1
done < <(printf '%s\n' "${DUMP_LINES[@]:1}")

if (( changed == 1 )); then
    cat "${tmp_history}" >>"${HISTORY}"
    touch "${CHANGED}"
fi
rm -f "${tmp_history}"

write_lastdb
prune_history

exit 0
