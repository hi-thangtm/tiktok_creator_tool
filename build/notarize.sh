#!/usr/bin/env bash
set -euo pipefail

DMG_PATH="${1:?Usage: build/notarize.sh path/to/app.dmg [keychain-profile]}"
PROFILE="${2:-TikTokCreatorToolNotary}"

if [[ ! -f "${DMG_PATH}" ]]; then
  echo "DMG not found: ${DMG_PATH}" >&2
  exit 1
fi

xcrun notarytool submit "${DMG_PATH}" \
  --keychain-profile "${PROFILE}" \
  --wait

xcrun stapler staple "${DMG_PATH}"
xcrun stapler validate "${DMG_PATH}"
