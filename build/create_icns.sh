#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ASSETS_DIR="${PROJECT_ROOT}/assets"
ICONSET="${ASSETS_DIR}/app_icon.iconset"
SOURCE_PNG="${ASSETS_DIR}/app_icon.png"
TARGET_ICNS="${ASSETS_DIR}/app_icon.icns"

if [[ -f "${TARGET_ICNS}" ]]; then
  echo "Icon exists: ${TARGET_ICNS}"
  exit 0
fi

if [[ ! -f "${SOURCE_PNG}" ]]; then
  echo "No assets/app_icon.icns or assets/app_icon.png found."
  echo "Build will use the default PyInstaller placeholder icon."
  exit 0
fi

mkdir -p "${ICONSET}"

sips -z 16 16 "${SOURCE_PNG}" --out "${ICONSET}/icon_16x16.png" >/dev/null
sips -z 32 32 "${SOURCE_PNG}" --out "${ICONSET}/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "${SOURCE_PNG}" --out "${ICONSET}/icon_32x32.png" >/dev/null
sips -z 64 64 "${SOURCE_PNG}" --out "${ICONSET}/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "${SOURCE_PNG}" --out "${ICONSET}/icon_128x128.png" >/dev/null
sips -z 256 256 "${SOURCE_PNG}" --out "${ICONSET}/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "${SOURCE_PNG}" --out "${ICONSET}/icon_256x256.png" >/dev/null
sips -z 512 512 "${SOURCE_PNG}" --out "${ICONSET}/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "${SOURCE_PNG}" --out "${ICONSET}/icon_512x512.png" >/dev/null
sips -z 1024 1024 "${SOURCE_PNG}" --out "${ICONSET}/icon_512x512@2x.png" >/dev/null

iconutil -c icns "${ICONSET}" -o "${TARGET_ICNS}"
echo "Created ${TARGET_ICNS}"
