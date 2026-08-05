#!/usr/bin/env bash
# AWGStat updater — usage: ./update.sh 1.1.1
set -euo pipefail

VERSION="${1:-}"
if [[ -z "${VERSION}" ]]; then
    echo "Usage: $0 <version>" >&2
    echo "Example: $0 1.1.1" >&2
    exit 1
fi

# Allow "v1.1.1" or "1.1.1"
VERSION="${VERSION#v}"

REPO="arma35/awgstat"
TAG="v${VERSION}"
ARCHIVE="awgstat-${VERSION}.tar.gz"
DIR="awgstat-${VERSION}"
URL="https://github.com/${REPO}/releases/download/${TAG}/${ARCHIVE}"

WORKDIR="$(mktemp -d)"
cleanup() {
    rm -rf "${WORKDIR}"
}
trap cleanup EXIT

cd "${WORKDIR}"

echo "Downloading ${URL}"
curl -fsSL -O "${URL}"

echo "Extracting ${ARCHIVE}"
tar -xzf "${ARCHIVE}"

if [[ ! -d "${DIR}" ]]; then
    echo "ERROR: expected directory ${DIR} after extract" >&2
    exit 1
fi

if [[ ! -f "${DIR}/install.sh" ]]; then
    echo "ERROR: ${DIR}/install.sh not found" >&2
    exit 1
fi

echo "Installing AWGStat ${VERSION}"
sudo bash "${DIR}/install.sh"

echo "Done. Temp files removed."
