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
AUTO_DISCOVER_NAMES="${AUTO_DISCOVER_NAMES:-1}"
AMNEZIA_CLIENTS_TABLE="${AMNEZIA_CLIENTS_TABLE:-/opt/amnezia/awg/clientsTable}"

LOG_DIR="${WORKDIR}/logs"
LOG_FILE="${LOG_DIR}/wgstats.log"
LOCK_FILE="${WORKDIR}/.lock"
CSV_VERSION="#WGSTAT:1"
CSV_HEADER="timestamp;date;time;peer;name;ip;rx_bytes;tx_bytes;interval;handshake"
UNKNOWN_LABEL="неизвестный"

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

# If names.map was edited (no traffic needed), ask htmlgen to redraw
mark_names_changed() {
    local stamp="${WORKDIR}/.names_mtime"
    [[ -f "${NAMES}" ]] || return 0
    local cur prev
    cur="$(stat -c '%Y' "${NAMES}" 2>/dev/null || stat -f '%m' "${NAMES}" 2>/dev/null || echo 0)"
    prev=0
    [[ -f "${stamp}" ]] && prev="$(tr -d '[:space:]' <"${stamp}" || true)"
    [[ "${prev}" =~ ^[0-9]+$ ]] || prev=0
    if (( cur > prev )); then
        printf '%s\n' "${cur}" >"${stamp}"
        touch "${CHANGED}"
        log "names.map changed → schedule HTML rebuild"
    fi
}

# names.map format: <wireguard-pubkey>:<display_name>
# Pubkeys are base64 and often end with '=' — separator MUST be ':' (colon).
parse_name_line() {
    local line="$1"
    local key val
    if [[ "${line}" == *":"* ]]; then
        key="${line%%:*}"
        val="${line#*:}"
    else
        # Legacy broken '=' format (key=name / key==name)
        key="${line%=*}"
        val="${line##*=}"
    fi
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    # recover from old bug: value stored as ":name"
    if [[ "${val}" == :* ]]; then
        val="${val:1}"
    fi
    printf '%s\t%s\n' "${key}" "${val}"
}

is_auto_unknown() {
    [[ "$1" == "${UNKNOWN_LABEL}" || "$1" == "${UNKNOWN_LABEL}-"* ]]
}

load_names() {
    declare -gA PEER_NAMES=()
    [[ -f "${NAMES}" ]] || return 0
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "${line}" ]] && continue
        [[ "${line}" != *"="* && "${line}" != *":"* ]] && continue
        local key val
        IFS=$'\t' read -r key val < <(parse_name_line "${line}")
        [[ -z "${key}" || -z "${val}" ]] && continue
        if [[ -z "${PEER_NAMES[${key}]:-}" ]]; then
            PEER_NAMES["${key}"]="${val}"
        elif is_auto_unknown "${PEER_NAMES[${key}]}" && ! is_auto_unknown "${val}"; then
            PEER_NAMES["${key}"]="${val}"
        fi
    done <"${NAMES}"
}

# Rewrite names.map to canonical "key:name", drop duplicates/junk
repair_names_map() {
    [[ -f "${NAMES}" ]] || return 0
    (( ${#PEER_NAMES[@]} == 0 )) && return 0

    local tmp key need=0 line
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "${line}" ]] && continue
        # Any legacy '='-only line or duplicate spam → rewrite
        if [[ "${line}" != *":"* ]]; then
            need=1
            break
        fi
    done <"${NAMES}"

    local total
    total="$(grep -cve '^[[:space:]]*$' "${NAMES}" || true)"
    if (( total > ${#PEER_NAMES[@]} )); then
        need=1
    fi

    # Also rewrite if any stored key would not round-trip as key:name
    if (( need == 0 )); then
        while IFS= read -r line || [[ -n "${line}" ]]; do
            line="${line%%#*}"
            line="${line#"${line%%[![:space:]]*}"}"
            [[ -z "${line}" ]] && continue
            local k v
            IFS=$'\t' read -r k v < <(parse_name_line "${line}")
            if [[ "${line}" != "${k}:${v}" ]]; then
                need=1
                break
            fi
        done <"${NAMES}"
    fi

    (( need == 0 )) && return 0

    tmp="$(mktemp)"
    {
        for key in "${!PEER_NAMES[@]}"; do
            printf '%s:%s\n' "${key}" "${PEER_NAMES[${key}]}"
        done
    } | LC_ALL=C sort >"${tmp}"
    mv "${tmp}" "${NAMES}"
    touch "${CHANGED}"
    log "names.map: repaired to key:name (${total:-?} lines → ${#PEER_NAMES[@]} peers)"
}

peer_name() {
    local peer="$1"
    if [[ -n "${PEER_NAMES[${peer}]:-}" ]]; then
        echo "${PEER_NAMES[${peer}]}"
    else
        echo ""
    fi
}

# Load Amnezia's own clientId -> clientName table once per collector cycle.
load_amnezia_names() {
    [[ "${AUTO_DISCOVER_NAMES}" == "1" ]] || return 1
    if [[ "${AMNEZIA_NAMES_LOADED:-0}" == "1" ]]; then
        return 0
    fi

    declare -gA AMNEZIA_NAMES=()
    AMNEZIA_NAMES_LOADED=1

    local raw parsed key val
    if ! raw="$(docker exec "${CONTAINER}" cat "${AMNEZIA_CLIENTS_TABLE}" 2>/dev/null)"; then
        log "names.map: cannot read Amnezia clientsTable: ${AMNEZIA_CLIENTS_TABLE}"
        return 1
    fi
    if ! parsed="$(printf '%s' "${raw}" | python3 "${SCRIPT_DIR}/amnezia_names.py" 2>/dev/null)"; then
        log "names.map: cannot parse Amnezia clientsTable"
        return 1
    fi

    while IFS=$'\t' read -r key val; do
        [[ -n "${key}" && -n "${val}" ]] || continue
        AMNEZIA_NAMES["${key}"]="${val}"
    done <<<"${parsed}"
    return 0
}

# For a peer absent from names.map, import Amnezia's clientName. Existing map
# entries are never overwritten, so names.map also remains a manual override.
discover_peer_name() {
    local peer="$1"
    local existing name tmp names_dir

    existing="$(peer_name "${peer}")"
    if [[ -n "${existing}" ]]; then
        echo "${existing}"
        return 0
    fi

    if ! load_amnezia_names; then
        echo ""
        return 0
    fi
    name="${AMNEZIA_NAMES[${peer}]:-}"
    if [[ -z "${name}" ]]; then
        echo ""
        return 0
    fi

    names_dir="$(dirname "${NAMES}")"
    mkdir -p "${names_dir}"
    tmp="$(mktemp "${names_dir}/.names.map.XXXXXX")"
    if [[ -f "${NAMES}" ]]; then
        cat "${NAMES}" >"${tmp}"
    fi
    printf '%s:%s\n' "${peer}" "${name}" >>"${tmp}"
    chmod 0644 "${tmp}"
    mv "${tmp}" "${NAMES}"

    PEER_NAMES["${peer}"]="${name}"
    touch "${CHANGED}"
    log "names.map: imported ${peer} → ${name} from Amnezia clientsTable"
    echo "${name}"
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
repair_names_map
mark_names_changed
init_history
read_lastdb

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
declare -A AMNEZIA_NAMES=()
AMNEZIA_NAMES_LOADED=0

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

    # A new peer is named immediately on first observation. clientsTable is
    # fetched lazily and at most once per collector cycle.
    if [[ -z "${PEER_NAMES[${peer}]:-}" ]]; then
        discover_peer_name "${peer}" >/dev/null || true
    fi

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
