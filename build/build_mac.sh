#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build script must run on macOS." >&2
  exit 1
fi

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${PROJECT_ROOT}/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

ARCH="$(uname -m)"
APP_PATH="${PROJECT_ROOT}/dist/TikTok Creator Tool.app"
COLLECT_PATH="${PROJECT_ROOT}/dist/TikTok Creator Tool"
WORK_PATH="${PROJECT_ROOT}/build/pyinstaller"
PYINSTALLER_CONFIG_DIR="${PROJECT_ROOT}/build/pyinstaller-config"

echo "Python: $("${PYTHON}" --version)"
echo "Architecture: ${ARCH}"

"${PYTHON}" - <<'PY'
import PyInstaller
import PySide6
import openpyxl
from importlib.metadata import version

print(f"PyInstaller: {PyInstaller.__version__}")
print(f"PySide6: {PySide6.__version__}")
print(f"Playwright: {version('playwright')}")
print(f"openpyxl: {openpyxl.__version__}")
PY

"${PYTHON}" -m compileall app.py core services ui workers tests scripts
"${PYTHON}" -m unittest discover -s tests -v

"${PROJECT_ROOT}/build/create_icns.sh"

rm -rf "${WORK_PATH}" "${PYINSTALLER_CONFIG_DIR}" "${PROJECT_ROOT}/dist"
mkdir -p "${WORK_PATH}" "${PYINSTALLER_CONFIG_DIR}"
export PYINSTALLER_CONFIG_DIR

"${PYTHON}" -m PyInstaller \
  --noconfirm \
  --clean \
  --workpath "${WORK_PATH}" \
  "${PROJECT_ROOT}/TikTokCreatorTool.spec"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Build failed: ${APP_PATH} not found." >&2
  exit 1
fi

if [[ -d "${COLLECT_PATH}" ]]; then
  rm -rf "${COLLECT_PATH}"
fi

if find "${APP_PATH}" -name "creators.db" -print -quit | grep -q .; then
  echo "FAIL: creators.db was packaged into the app." >&2
  exit 1
fi

if find "${APP_PATH}" -path "*browser_data*" -print -quit | grep -q .; then
  echo "FAIL: browser_data was packaged into the app." >&2
  exit 1
fi

if find "${APP_PATH}" \( -name "*.xlsx" -o -name "*.xls" -o -name ".env" \) -print -quit | grep -q .; then
  echo "FAIL: private export/env files were packaged into the app." >&2
  exit 1
fi

echo "Main executable:"
file "${APP_PATH}/Contents/MacOS/TikTok Creator Tool"

if command -v codesign >/dev/null 2>&1; then
  if codesign --verify --deep --strict --verbose=2 "${APP_PATH}"; then
    echo "codesign verify: PASS"
  else
    echo "codesign verify: NOT RUN/PASS for unsigned development build"
  fi
fi

echo "Built app: ${APP_PATH}"

echo "Creating DMG..."
"${PROJECT_ROOT}/build/create_dmg.sh"
