# TikTok Creator Tool Distribution

## Development build

```bash
./build/build_mac.sh
./build/create_dmg.sh
```

Output:

- `dist/TikTok Creator Tool.app`
- `dist/TikTok-Creator-Tool-1.0.0-<arch>.dmg`

## Production distribution

1. Install a valid `Developer ID Application` certificate in Keychain.
2. Build the app with `./build/build_mac.sh`.
3. Sign the app with `./build/sign_app.sh`.
4. Create the DMG with `./build/create_dmg.sh`.
5. Notarize and staple the DMG with:

```bash
./build/notarize.sh "dist/TikTok-Creator-Tool-1.0.0-<arch>.dmg" TikTokCreatorToolNotary
```

Create the notary profile once outside source control:

```bash
xcrun notarytool store-credentials TikTokCreatorToolNotary
```

6. Verify with `./build/verify_release.sh`.

## User install flow

1. Open the DMG.
2. Drag `TikTok Creator Tool.app` into `Applications`.
3. Open the app.
4. Install Google Chrome if the app asks for it.
5. Log in to TikTok manually in Chrome.

The app stores runtime data outside the bundle:

- `~/Library/Application Support/TikTok Creator Tool/data/creators.db`
- `~/Library/Application Support/TikTok Creator Tool/browser_data/`
- `~/Library/Application Support/TikTok Creator Tool/logs/app.log`
- `~/Documents/TikTok Creator Tool/Exports/`
