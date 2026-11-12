# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for dwg2pdf.

Builds one onedir bundle (dist/dwg2pdf/) containing two executables:
  dwg2pdf      — console CLI
  dwg2pdf-gui  — windowed Tk GUI

Build:  pyinstaller packaging/dwg2pdf.spec --noconfirm
"""
import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for _pkg in ("ezdxf", "pymupdf"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Bundled CJK font (always present in the source tree).
_font_dir = os.path.join(SPECPATH, os.pardir, "src", "dwg2pdf", "font_data")
if os.path.isdir(_font_dir):
    datas.append((_font_dir, os.path.join("dwg2pdf", "font_data")))

# Bundled DWG engine: populated by the Linux release job (LibreDWG build);
# absent elsewhere — detection then falls back to ODA / system dwg2dxf.
_engine_dir = os.path.join(SPECPATH, os.pardir, "src", "dwg2pdf", "engine")
if os.path.isdir(_engine_dir) and os.listdir(_engine_dir):
    datas.append((_engine_dir, os.path.join("dwg2pdf", "engine")))

_icon_path = os.path.join(SPECPATH, "icon.ico")
icon = _icon_path if os.path.exists(_icon_path) else None

_common = dict(
    pathex=[os.path.join(SPECPATH, os.pardir, "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # matplotlib is only needed by ezdxf's *matplotlib* backend, which this
    # tool never imports (PDF export uses the PyMuPDF backend). onnxruntime is
    # an optional PyMuPDF extra (OCR/AI features) that we never call.
    excludes=[
        "IPython", "notebook", "sphinx", "pandas",
        "matplotlib", "PyQt5", "PyQt6", "PySide6",
        "onnxruntime",
    ],
    noarchive=False,
)

a_cli = Analysis([os.path.join(SPECPATH, "run_cli.py")], **_common)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    exclude_binaries=True,
    name="dwg2pdf",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=icon,
)

a_gui = Analysis([os.path.join(SPECPATH, "run_gui.py")], **_common)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    exclude_binaries=True,
    name="dwg2pdf-gui",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)

coll = COLLECT(
    exe_cli,
    exe_gui,
    a_cli.binaries,
    a_cli.datas,
    a_gui.binaries,
    a_gui.datas,
    strip=False,
    upx=False,
    name="dwg2pdf",
)
