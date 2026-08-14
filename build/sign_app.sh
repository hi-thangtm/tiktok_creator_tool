#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_PATH="${1:-${PROJECT_ROOT}/dist/TikTok Creator Tool.app}"
ENTITLEMENTS="${PROJECT_ROOT}/build/entitlements.plist"

IDENTITY="${DEVELOPER_ID_APPLICATION:-}"
if [[ -z "${IDENTITY}" ]]; then
  IDENTITY="$(security find-identity -v -p codesigning | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
fi

if [[ -z "${IDENTITY}" ]]; then
  echo "No Developer ID Application certificate found." >&2
  exit 2
fi

if [[ ! -d "${APP_PATH}" ]]; then
  echo "App not found: ${APP_PATH}" >&2
  exit 1
fi

echo "Signing with: ${IDENTITY}"

while IFS= read -r -d '' item; do
  codesign --force --options runtime --timestamp \
    --entitlements "${ENTITLEMENTS}" \
    --sign "${IDENTITY}" \
    "${item}"
done < <(
  find "${APP_PATH}/Contents/Frameworks" \
    \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) \
    -print0 2>/dev/null || true
)

codesign --force --options runtime --timestamp \
  --entitlements "${ENTITLEMENTS}" \
  --sign "${IDENTITY}" \
  "${APP_PATH}"

codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
codesign -dv --verbose=4 "${APP_PATH}"
