# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = []
for asset in ("logo.ico", "logo.png"):
    if os.path.exists(asset):
        datas.append((asset, "."))

# Collect runtime data assets (themes, locales, CA certificates)
for pkg in ("customtkinter", "ytmusicapi", "certifi"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# FFmpeg is installed automatically once into %APPDATA%\DjaelYTPlaylistDWNLD\bin
binaries = []

hiddenimports = [
    "customtkinter",
    "darkdetect",
    "PIL",
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.ImageTk",
    "ytmusicapi",
    "mutagen",
    "mutagen.mp3",
    "mutagen.id3",
    "requests",
    "certifi",
]
try:
    hiddenimports += collect_submodules("yt_dlp.extractor")
except Exception:
    pass

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "scipy", "cv2", "playwright", "streamlit", "pymongo"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Using --onedir (exclude_binaries=True + COLLECT) instead of --onefile:
# - Instant startup (< 1s, no temp self-extraction)
# - Dramatically fewer antivirus false positives (no dropper-like %TEMP% unpacking)
# - Packaged cleanly into both a Windows Installer (Inno Setup) and a portable .zip
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DjaelYTPlaylistDWNLD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="logo.ico" if os.path.exists("logo.ico") else None,
    version="file_version_info.txt" if os.path.exists("file_version_info.txt") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DjaelYTPlaylistDWNLD",
)
