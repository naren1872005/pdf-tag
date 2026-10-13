import os
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = []
binaries = []
hiddenimports = []
datas += copy_metadata('google-genai')
tmp_ret = collect_all('google')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += [
    'renderer.font_data',
    'renderer.font_data.liberation_sans_regular',
    'renderer.font_data.liberation_sans_bold',
    'renderer.font_data.liberation_sans_italic',
    'renderer.font_data.liberation_sans_bolditalic',
    'renderer.font_data.liberation_serif_regular',
    'renderer.font_data.liberation_serif_bold',
    'renderer.font_data.liberation_serif_italic',
    'renderer.font_data.liberation_serif_bolditalic',
    # modules imported inside functions (make sure the build always contains them)
    'app.branding',
    'app.logging_setup',
    'app.activation',
    'gui.struct_tag_editor',
    'renderer.page_geometry',
    'export.structure_enforcer',
    'fontTools.subset',
    'fontTools.agl',
    'fontTools.ttLib',
    'win32crypt',
    'pywintypes',
]
# Every module of the project's own packages. Some are discovered at run time
# by scanning their package folder (e.g. plugins.visual.rules), which
# PyInstaller's import analysis cannot see. Listed from the folder tree, so it
# does not depend on importing anything at build time.
import os as _os
for _pkg in ('plugins', 'rules', 'engine', 'export', 'renderer', 'metadata', 'graph', 'parser', 'gui', 'app', 'models'):
    _base = _os.path.join(SPECPATH, _pkg)
    for _root, _dirs, _files in _os.walk(_base):
        _dirs[:] = [d for d in _dirs if d != '__pycache__']
        for _fn in _files:
            if not _fn.endswith('.py'):
                continue
            _rel = _os.path.relpath(_os.path.join(_root, _fn[:-3]), SPECPATH)
            _mod = _rel.replace(_os.sep, '.')
            if _mod.endswith('.__init__'):
                _mod = _mod[:-len('.__init__')]
            hiddenimports.append(_mod)
# data files: only patterns that exist are added (a missing optional file,
# e.g. old visual*.json debug reports, must not stop the build)
import glob as _glob
for _pat, _dest in (('config/*', 'config'), ('visual*.json', '.'), ('icon.ico', '.'), ('icon.png', '.')):
    if _glob.glob(os.path.join(SPECPATH, _pat)):
        datas.append((os.path.join(SPECPATH, _pat), _dest))


a = Analysis(
    ['run.py'],
    pathex=['.'],
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
    [],
    exclude_binaries=True,
    name='tagpilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    icon='icon.ico',
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='tagpilot',
)
