#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_PATH="${1:-${PROJECT_ROOT}/dist/TikTok Creator Tool.app}"
DMG_PATH="${2:-}"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "App not found: ${APP_PATH}" >&2
  exit 1
fi

if find "${APP_PATH}" -name "creators.db" -print -quit | grep -q .; then
  echo "FAIL: creators.db found in app." >&2
  exit 1
fi

if find "${APP_PATH}" -path "*browser_data*" -print -quit | grep -q .; then
  echo "FAIL: browser_data found in app." >&2
  exit 1
fi

file "${APP_PATH}/Contents/MacOS/TikTok Creator Tool"
codesign --verify --deep --strict --verbose=2 "${APP_PATH}" || true
spctl --assess --type execute --verbose=4 "${APP_PATH}" || true

if [[ -n "${DMG_PATH}" ]]; then
  if [[ ! -f "${DMG_PATH}" ]]; then
    echo "DMG not found: ${DMG_PATH}" >&2
    exit 1
  fi
  shasum -a 256 "${DMG_PATH}"
fi
