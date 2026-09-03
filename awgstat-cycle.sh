#!/usr/bin/env bash
# Run one serialized AWGStat collection/report cycle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config
source "${SCRIPT_DIR}/config"

PYTHON="${PYTHON:-/usr/bin/python3}"
CYCLE_LOCK="${WORKDIR}/.cycle.lock"

mkdir -p "${WORKDIR}"
exec 8>"${CYCLE_LOCK}"
flock -n 8 || exit 0

if [[ "${1:-}" == "--html-only" ]]; then
    shift
    exec "${PYTHON}" "${SCRIPT_DIR}/htmlgen.py" "$@"
fi

"${SCRIPT_DIR}/wgstats.sh"

# Archive pages stay on the established five-minute cadence. The lightweight
# ONLINE tree and root index are refreshed on every other collection cycle.
report_clock="$(TZ="${REPORT_TZ:-Europe/Moscow}" date '+%H:%M')"
if [[ "${report_clock}" == "00:01" ]]; then
    exec "${PYTHON}" "${SCRIPT_DIR}/htmlgen.py" --force
fi
minute="$(date '+%M')"
if (( 10#${minute} % 5 == 0 )); then
    exec "${PYTHON}" "${SCRIPT_DIR}/htmlgen.py" --scheduled
fi
exec "${PYTHON}" "${SCRIPT_DIR}/htmlgen.py" --online
