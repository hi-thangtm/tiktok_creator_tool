# TikTok Creator Tool Build Report

## Build Summary

- App name: TikTok Creator Tool
- Version: 1.0.0
- Bundle identifier: com.tiktokcreatortool.app
- Build date: 2026-08-14 08:09:47 +07
- Build machine: macOS 26.5.2 (Build 25F84)
- CPU architecture: arm64
- Build architecture: arm64
- Minimum tested macOS version: macOS 26.5.2 only
- App path: `dist/TikTok Creator Tool.app`
- DMG path: `dist/TikTok-Creator-Tool-1.0.0-arm64.dmg`
- App size: 211M
- DMG size: 79M

## Toolchain

- Python: 3.14.6
- Python architecture: arm64
- PyInstaller: 6.22.0
- PySide6: 6.11.1
- Playwright: 1.62.0
- openpyxl: 3.1.5
- Google Chrome: installed at `/Applications/Google Chrome.app`

## Runtime Paths

- App Support: `~/Library/Application Support/TikTok Creator Tool/`
- Database: `~/Library/Application Support/TikTok Creator Tool/data/creators.db`
- Browser profile: `~/Library/Application Support/TikTok Creator Tool/browser_data/`
- Logs: `~/Library/Application Support/TikTok Creator Tool/logs/app.log`
- Excel exports: `~/Documents/TikTok Creator Tool/Exports/`

The app bundle does not write runtime data into `TikTok Creator Tool.app/Contents/...`.

## Artifact Architecture

- Main executable: arm64
- Bundled Python framework: arm64
- Playwright driver node: arm64
- Qt cocoa platform plugin: arm64
- Universal2: NOT CLAIMED

## Tests

- Syntax/import check: PASS
- Unit tests: PASS, 32/32
- Development `python3 app.py --smoke-test`: PASS
- `.app` launch via `open "dist/TikTok Creator Tool.app"`: PASS
- Packaged GUI startup/dashboard DB read: PASS, 534 creators
- Packaged search check: PASS
- Packaged contact filter check: PASS
- Packaged creator detail dialog construction: PASS
- Packaged Excel export: PASS, 152 creators exported to `/private/tmp`
- Packaged first-run clean App Support: PASS, count = 0
- Packaged BrowserWorker Chrome launch: PASS
- Packaged CollectorWorker Chrome launch + safe stop: PASS, temporary App Support
- Packaged ContactWorker Chrome launch + safe stop: PASS, temporary App Support
- CAPTCHA dialog runtime: NOT RUN, no CAPTCHA appeared during smoke tests
- DMG creation: PASS
- DMG mount/content check: PASS
- App personal-data audit: PASS
- DMG personal-data audit: PASS
- Code-sign verification: PASS for PyInstaller ad-hoc development signature
- Developer ID Application certificate: FAIL/NOT AVAILABLE, `0 valid identities found`
- Production signing with Hardened Runtime: NOT RUN
- Notarization: NOT RUN
- Staple: NOT RUN
- Gatekeeper production assessment: FAIL/NOT APPLICABLE for unsigned/ad-hoc development build

## Personal Data Audit

Checked app bundle and mounted DMG for:

- `creators.db`: not found
- `browser_data`: not found
- Excel files: not found
- `.env`: not found
- obvious Chrome cookies/login-data filenames: not found

Result: PASS. No production database, browser profile/session, Excel export, log, token, or credential file was packaged.

## Checksums

```text
b43c318c524ee6a12c09a11db9261aaa864ac71ea1b2651e760d781f82f043b9  dist/TikTok-Creator-Tool-1.0.0-arm64.dmg
```

## Signing Checkpoint

No valid `Developer ID Application` certificate is installed on this Mac. Production distribution must stop here until a Developer ID certificate and notarytool keychain profile are available.

Next production steps:

1. Install a valid Developer ID Application certificate.
2. Create a notarytool keychain profile, for example `TikTokCreatorToolNotary`.
3. Run `./build/sign_app.sh`.
4. Run `./build/create_dmg.sh`.
5. Run `./build/notarize.sh "dist/TikTok-Creator-Tool-1.0.0-arm64.dmg" TikTokCreatorToolNotary`.
6. Run `./build/verify_release.sh`.
