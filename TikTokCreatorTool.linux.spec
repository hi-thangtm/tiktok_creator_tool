# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

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
EXE_NAME = "TikTokCreatorTool"

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
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=EXE_NAME,
)
