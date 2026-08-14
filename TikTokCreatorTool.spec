# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import platform

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


project_root = Path.cwd()
version_values = {}
exec(
    (project_root / "core" / "version.py").read_text(
        encoding="utf-8",
    ),
    version_values,
)

APP_NAME = version_values["APP_NAME"]
APP_VERSION = version_values["APP_VERSION"]
BUNDLE_ID = version_values["BUNDLE_ID"]

icon_path = project_root / "assets" / "app_icon.icns"
icon = str(icon_path) if icon_path.exists() else None

playwright_datas = collect_data_files(
    "playwright",
    include_py_files=False,
    includes=[
        "driver/**",
    ],
)
metadata_datas = []
for package_name in (
    "playwright",
    "openpyxl",
    "PySide6_Essentials",
):
    try:
        metadata_datas += copy_metadata(package_name)
    except Exception:
        pass

hiddenimports = []
hiddenimports += collect_submodules("playwright")

build_arch = platform.machine()
if build_arch not in ("arm64", "x86_64"):
    build_arch = None

block_cipher = None


a = Analysis(
    ["app.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=playwright_datas + metadata_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
        "matplotlib",
        "numpy",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=build_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=icon,
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.business",
    },
)
