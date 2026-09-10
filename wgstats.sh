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
ONLINE_STATE="${ONLINE_STATE:-${WORKDIR}/online.csv}"
AMNEZIA_CLIENTS_TABLE="${AMNEZIA_CLIENTS_TABLE:-/opt/amnezia/awg/clientsTable}"

LOG_DIR="${WORKDIR}/logs"
LOG_FILE="${LOG_DIR}/wgstats.log"
LOCK_FILE="${WORKDIR}/.lock"
CLIENT_NAMES_FINGERPRINT="${WORKDIR}/.client_names.sha256"
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

# Amnezia is the authoritative source for current peer names. No names.map is
# maintained: renamed peers immediately use the current clientName, while
# revoked peers keep their last known name only in history.csv.
load_amnezia_names() {
    declare -gA AMNEZIA_NAMES=()

    local raw parsed normalized key val fingerprint previous
    if ! raw="$(docker exec "${CONTAINER}" cat "${AMNEZIA_CLIENTS_TABLE}" 2>/dev/null)"; then
        log "WARNING: cannot read Amnezia clientsTable: ${AMNEZIA_CLIENTS_TABLE}"
        return 1
    fi
    if ! parsed="$(printf '%s' "${raw}" | python3 "${SCRIPT_DIR}/amnezia_names.py" 2>/dev/null)"; then
        log "WARNING: cannot parse Amnezia clientsTable"
        return 1
    fi

    normalized="$(printf '%s\n' "${parsed}" | sed '/^[[:space:]]*$/d' | LC_ALL=C sort)"
    while IFS=$'\t' read -r key val; do
        [[ -n "${key}" && -n "${val}" ]] || continue
        AMNEZIA_NAMES["${key}"]="${val}"
    done <<<"${normalized}"

    fingerprint="$(printf '%s' "${normalized}" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
    previous=""
    [[ -f "${CLIENT_NAMES_FINGERPRINT}" ]] && previous="$(tr -d '[:space:]' <"${CLIENT_NAMES_FINGERPRINT}" || true)"
    if [[ "${fingerprint}" != "${previous}" ]]; then
        printf '%s\n' "${fingerprint}" >"${CLIENT_NAMES_FINGERPRINT}"
        touch "${CHANGED}"
        log "Amnezia client names changed → schedule HTML rebuild"
    fi
    return 0
}

peer_name() {
    local peer="$1"
    printf '%s' "${AMNEZIA_NAMES[${peer}]:-}"
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
        # Counter reset after container/interface restart.
        echo "${current}"
    fi
}

write_lastdb() {
    local tmp peer
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

init_history
read_lastdb

declare -A AMNEZIA_NAMES=()
load_amnezia_names || true

NOW="$(date +%s)"
DATE="$(date '+%Y-%m-%d')"
TIME="$(date '+%H:%M:%S')"

declare -A CURRENT_RX=()
declare -A CURRENT_TX=()
declare -A CURRENT_IP=()
declare -A CURRENT_HS=()
declare -A CURRENT_DRX=()
declare -A CURRENT_DTX=()
declare -A CURRENT_INTERVAL=()

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
    CURRENT_DRX["${peer}"]=0
    CURRENT_DTX["${peer}"]=0
    CURRENT_INTERVAL["${peer}"]=0

    if [[ -z "${LAST_RX[${peer}]:-}" ]]; then
        continue
    fi

    drx="$(calc_delta "${rx}" "${LAST_RX[${peer}]}")"
    dtx="$(calc_delta "${tx}" "${LAST_TX[${peer}]}")"
    interval=$((NOW - LAST_TS[${peer}]))
    (( interval < 0 )) && interval=0
    CURRENT_DRX["${peer}"]="${drx}"
    CURRENT_DTX["${peer}"]="${dtx}"
    CURRENT_INTERVAL["${peer}"]="${interval}"

    if (( drx == 0 && dtx == 0 )); then
        continue
    fi

    name="$(peer_name "${peer}")"
    name="${name//;/,}"
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

clean_online_field() {
    local value="$1"
    value="${value//$'\r'/ }"
    value="${value//$'\n'/ }"
    value="${value//;/,}"
    printf '%s' "${value}"
}

write_online_state() {
    local state_dir tmp peer name ip
    state_dir="$(dirname "${ONLINE_STATE}")"
    mkdir -p "${state_dir}"
    tmp="$(mktemp "${state_dir}/.online.csv.XXXXXX")"
    {
        printf '#AWGSTAT-ONLINE:1;%s\n' "${NOW}"
        echo "sample_timestamp;peer;name;ip;rx_bytes;tx_bytes;interval;handshake;rx_total;tx_total"
        printf '%s\n' "${!CURRENT_RX[@]}" | LC_ALL=C sort | while IFS= read -r peer; do
            [[ -n "${peer}" ]] || continue
            name="$(clean_online_field "$(peer_name "${peer}")")"
            ip="$(clean_online_field "${CURRENT_IP[${peer}]:-}")"
            printf '%s;%s;%s;%s;%s;%s;%s;%s;%s;%s\n' \
                "${NOW}" "${peer}" "${name}" "${ip}" \
                "${CURRENT_DRX[${peer}]:-0}" "${CURRENT_DTX[${peer}]:-0}" \
                "${CURRENT_INTERVAL[${peer}]:-0}" "${CURRENT_HS[${peer}]:-0}" \
                "${CURRENT_RX[${peer}]:-0}" "${CURRENT_TX[${peer}]:-0}"
        done
    } >"${tmp}"
    mv "${tmp}" "${ONLINE_STATE}"
}

write_online_state
write_lastdb
prune_history

exit 0
