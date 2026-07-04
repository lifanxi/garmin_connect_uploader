# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


block_cipher = None
project_root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
icon_file = os.path.join(project_root, "assets", "icons", "gcu-icon.ico")

datas = []
binaries = []
hiddenimports = collect_submodules("gcu")
hiddenimports += collect_submodules("fit_tool.profile")

for package in ("timezonefinder", "geonamescache", "tzdata"):
    hiddenimports += collect_submodules(package)
    datas += collect_data_files(package)
    binaries += collect_dynamic_libs(package)

for package in ("certifi",):
    datas += collect_data_files(package)

for package in ("garth", "pydantic", "pydantic_core", "annotated_types"):
    datas += collect_data_files(package, include_py_files=True)

datas += [
    (os.path.join(project_root, "assets", "icons", "gcu-icon.png"), os.path.join("assets", "icons")),
    (icon_file, os.path.join("assets", "icons")),
]


def app_analysis(script_name):
    return Analysis(
        [os.path.join(project_root, script_name)],
        pathex=[project_root],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=["logfire"],
        noarchive=False,
        optimize=0,
    )


gui_a = app_analysis("gcu_gui.py")
cli_a = app_analysis("gcu_cli.py")

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="GarminConnectUploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="gcu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    gui_a.binaries,
    gui_a.datas,
    cli_a.binaries,
    cli_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GarminConnectUploader",
)
