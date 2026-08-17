#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This build script must run on Linux." >&2
  exit 1
fi

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${PROJECT_ROOT}/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

ARCH="$(uname -m)"

DIST_ROOT="${PROJECT_ROOT}/dist/linux"
OUTPUT_DIR="${DIST_ROOT}/TikTokCreatorTool"
EXE_PATH="${OUTPUT_DIR}/TikTokCreatorTool"

WORK_PATH="${PROJECT_ROOT}/build/pyinstaller-linux"
PYINSTALLER_CONFIG_DIR="${PROJECT_ROOT}/build/pyinstaller-config-linux"

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

echo "Compiling Python source..."
"${PYTHON}" -m compileall \
  app.py \
  core \
  services \
  ui \
  workers \
  tests \
  scripts

echo "Running unit tests..."
"${PYTHON}" -m unittest discover -s tests -v

echo "Cleaning previous Linux build..."
rm -rf \
  "${WORK_PATH}" \
  "${PYINSTALLER_CONFIG_DIR}" \
  "${DIST_ROOT}"

mkdir -p \
  "${WORK_PATH}" \
  "${PYINSTALLER_CONFIG_DIR}" \
  "${DIST_ROOT}"

export PYINSTALLER_CONFIG_DIR

echo "Building Linux application..."
"${PYTHON}" -m PyInstaller \
  --noconfirm \
  --clean \
  --workpath "${WORK_PATH}" \
  --distpath "${DIST_ROOT}" \
  "${PROJECT_ROOT}/TikTokCreatorTool.linux.spec"

if [[ ! -d "${OUTPUT_DIR}" ]]; then
  echo "FAIL: Linux output directory was not created: ${OUTPUT_DIR}" >&2
  exit 1
fi

if [[ ! -f "${EXE_PATH}" ]]; then
  echo "FAIL: Linux executable was not created: ${EXE_PATH}" >&2
  exit 1
fi

if [[ ! -x "${EXE_PATH}" ]]; then
  echo "FAIL: Linux executable does not have execute permission." >&2
  exit 1
fi

echo "Checking for private/local data..."

if find "${OUTPUT_DIR}" -name "creators.db" -print -quit | grep -q .; then
  echo "FAIL: creators.db was packaged into the application." >&2
  exit 1
fi

if find "${OUTPUT_DIR}" -path "*browser_data*" -print -quit | grep -q .; then
  echo "FAIL: browser_data was packaged into the application." >&2
  exit 1
fi

if find "${OUTPUT_DIR}" \
  \( -name "*.xlsx" -o -name "*.xls" -o -name ".env" \) \
  -print -quit | grep -q .; then
  echo "FAIL: private export/env files were packaged into the application." >&2
  exit 1
fi

echo "Linux executable:"
file "${EXE_PATH}"

echo "Output contents:"
find "${OUTPUT_DIR}" -maxdepth 2 -type f -print

echo
echo "Linux build completed successfully."
echo "Built application: ${OUTPUT_DIR}"
