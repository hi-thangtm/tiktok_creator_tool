#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "DMG creation must run on macOS." >&2
  exit 1
fi

PYTHON="${PROJECT_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

APP_NAME="$("${PYTHON}" - <<'PY'
from core.version import APP_NAME
print(APP_NAME)
PY
)"
APP_VERSION="$("${PYTHON}" - <<'PY'
from core.version import APP_VERSION
print(APP_VERSION)
PY
)"
ARCH="$(uname -m)"
APP_PATH="${PROJECT_ROOT}/dist/${APP_NAME}.app"
DMG_ROOT="${PROJECT_ROOT}/build/dmg-root"
DMG_PATH="${PROJECT_ROOT}/dist/TikTok-Creator-Tool-${APP_VERSION}-${ARCH}.dmg"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "App not found: ${APP_PATH}" >&2
  exit 1
fi

rm -rf "${DMG_ROOT}" "${DMG_PATH}"
mkdir -p "${DMG_ROOT}"
ditto "${APP_PATH}" "${DMG_ROOT}/${APP_NAME}.app"
ln -s /Applications "${DMG_ROOT}/Applications"

if find "${DMG_ROOT}" -name "creators.db" -print -quit | grep -q .; then
  echo "FAIL: creators.db would be packaged into DMG." >&2
  exit 1
fi

if find "${DMG_ROOT}" -path "*browser_data*" -print -quit | grep -q .; then
  echo "FAIL: browser_data would be packaged into DMG." >&2
  exit 1
fi

hdiutil create \
  -volname "${APP_NAME}" \
  -srcfolder "${DMG_ROOT}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}"

echo "Created DMG: ${DMG_PATH}"
