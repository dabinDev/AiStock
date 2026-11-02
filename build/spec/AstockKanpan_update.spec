# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('E:\\Github\\Astock\\kanpan-tool', 'kanpan-tool'), ('E:\\Github\\Astock\\market-overview-exporter', 'market-overview-exporter'), ('E:\\Github\\Astock\\duanxian-jingjia-exporter', 'duanxian-jingjia-exporter'), ('E:\\Github\\Astock\\duanxian-workflow', 'duanxian-workflow'), ('E:\\Github\\Astock\\duanxian-yidong-pool', 'duanxian-yidong-pool')]
binaries = []
hiddenimports = ['uuid', 'ctypes', 'ctypes.wintypes', 'json', 'threading', 'subprocess', 'http.server', 'urllib.parse', 'urllib.request', 'requests', 'playwright', 'playwright.sync_api', 'zoneinfo', 'tkinter', 'tkinter.messagebox']
tmp_ret = collect_all('requests')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['E:\\Github\\Astock\\scripts\\run_kanpan_tool.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AstockKanpan_update',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['E:\\Github\\Astock\\scripts\\assets\\astock-kanpan.ico'],
)
