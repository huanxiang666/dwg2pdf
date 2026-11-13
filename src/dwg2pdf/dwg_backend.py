"""External DWG -> DXF converter detection and execution.

DWG is a proprietary binary format. We do not parse it ourselves; instead we
drive one of two well-known converters when installed:

1. ODA File Converter (free GUI/CLI tool from the Open Design Alliance,
   Windows/Linux/macOS). Preferred when present because of its fidelity.
2. GNU LibreDWG ``dwg2dxf`` (``apt install libredwg-tools`` on Debian/Ubuntu,
   or built from source).

Set ``DWG2PDF_CONVERTER=/path/to/executable`` to force a specific converter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

KIND_ODA = "oda"
KIND_LIBREDWG = "libredwg"

#: Environment variable that forces a specific converter executable.
ENV_CONVERTER = "DWG2PDF_CONVERTER"

# Common ODA File Converter install locations on Windows (versioned folders).
_WINDOWS_ODA_GLOBS = (
    "C:/Program Files/ODA/*/ODAFileConverter.exe",
    "C:/Program Files (x86)/ODA/*/ODAFileConverter.exe",
)

INSTALL_HINT = """\
No DWG converter found — .dwg input requires one of:
  1) ODA File Converter (free):  https://www.opendesign.com/guestfiles/oda_file_converter
     Windows: run the installer.  Linux: install the .deb/.rpm/AppImage, or set
     DWG2PDF_CONVERTER=/path/to/ODAFileConverter
  2) GNU LibreDWG (dwg2dxf):
     Debian/Ubuntu:  sudo apt install libredwg-tools
     From source:    https://www.gnu.org/software/libredwg/
DXF files convert without any external tool.
没有找到 DWG 转换器。DWG 输入需要安装 ODA File Converter（免费）或 GNU LibreDWG
（apt install libredwg-tools）。DXF 文件无需任何外部工具即可转换。"""


class DwgConvertError(RuntimeError):
    """The external converter failed on one file."""


@dataclass(frozen=True)
class DwgConverter:
    """A detected external DWG -> DXF converter."""

    kind: str  # KIND_ODA | KIND_LIBREDWG
    path: str

    @property
    def display_name(self) -> str:
        if self.kind == KIND_ODA:
            return f"ODA File Converter ({self.path})"
        return f"LibreDWG dwg2dxf ({self.path})"


def _infer_kind(path: str) -> str:
    base = Path(path).name.lower()
    if "dwg2dxf" in base:
        return KIND_LIBREDWG
    if "odafileconverter" in base:
        return KIND_ODA
    # Unknown name: assume ODA-style directory CLI only if it answers like ODA.
    # Default to libredwg-style (simple -o CLI) is unsafe; report as ODA and
    # let the conversion error surface if wrong.
    return KIND_ODA


def _find_oda() -> str | None:
    try:
        from ezdxf.addons import odafc
    except Exception:
        odafc = None
    if odafc is not None:
        try:
            if odafc.is_installed():
                if sys.platform == "win32":
                    return odafc.get_win_exec_path()
                unix_path = odafc.get_unix_exec_path()
                if unix_path and Path(unix_path).is_file():
                    return unix_path
                return shutil.which("ODAFileConverter")
        except Exception:
            pass
    found = shutil.which("ODAFileConverter")
    if found:
        return found
    if sys.platform == "win32":
        import glob as globmod

        for pattern in _WINDOWS_ODA_GLOBS:
            matches = sorted(globmod.glob(pattern))
            if matches:
                return matches[-1]  # newest version folder sorts last
    return None


def bundled_dwg2dxf() -> str | None:
    """Path to the dwg2dxf engine shipped INSIDE this installation, if any.

    Linux release bundles carry a pinned LibreDWG dwg2dxf build under
    ``dwg2pdf/engine/`` (populated by the release workflow, included by the
    PyInstaller spec). Windows bundles do not embed the engine (license +
    size); the in-app Engine Assistant downloads the official LibreDWG
    win64 build into the user engine dir instead — see
    ``_user_downloaded_engine``.
    """
    exe = "dwg2dxf.exe" if sys.platform == "win32" else "dwg2dxf"
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "dwg2pdf" / "engine")
    candidates.append(Path(__file__).resolve().parent / "engine")
    for cand in candidates:
        p = cand / exe
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _user_downloaded_engine() -> str | None:
    """Engine installed by the in-app downloader (engine_download module).

    Windows users get ``libredwg-win64/dwg2dxf.exe`` (+ DLLs) from the
    official LibreDWG GitHub release — no ODA install needed anymore.
    """
    try:
        from .engine_download import user_engine_dir
    except Exception:
        return None
    exe = "dwg2dxf.exe" if sys.platform == "win32" else "dwg2dxf"
    root = user_engine_dir()
    for cand in (root / "libredwg-win64" / exe, root / exe):
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except OSError:
            continue
    return None


def detect_converter() -> DwgConverter | None:
    """Detect the best available DWG converter, or None.

    Order: ``DWG2PDF_CONVERTER`` env var, ODA File Converter (highest
    fidelity), the bundled dwg2dxf engine (Linux releases), the engine
    installed by the in-app downloader, then any ``dwg2dxf`` on PATH.
    """
    env = os.environ.get(ENV_CONVERTER, "").strip()
    if env:
        path = shutil.which(env) or (env if Path(env).is_file() else None)
        if path:
            return DwgConverter(kind=_infer_kind(path), path=str(path))
        # Env var set but points at nothing executable: warn (do not fail
        # silently) then fall through to auto-detection.
        print(
            f"warning: {ENV_CONVERTER}={env!r} is not an executable file; "
            "falling back to auto-detection",
            file=sys.stderr,
        )
    oda = _find_oda()
    if oda:
        return DwgConverter(kind=KIND_ODA, path=oda)
    bundled = bundled_dwg2dxf()
    if bundled:
        return DwgConverter(kind=KIND_LIBREDWG, path=bundled)
    downloaded = _user_downloaded_engine()
    if downloaded:
        return DwgConverter(kind=KIND_LIBREDWG, path=downloaded)
    dwg2dxf = shutil.which("dwg2dxf")
    if dwg2dxf:
        return DwgConverter(kind=KIND_LIBREDWG, path=dwg2dxf)
    return None


def converter_version(converter: DwgConverter, timeout: int = 15) -> str:
    """Best-effort version string of the external converter (for --check)."""
    try:
        if converter.kind == KIND_LIBREDWG:
            proc = subprocess.run(
                [converter.path, "--version"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr) else "unknown"
        # ODA File Converter is a Qt app; running it without args opens a GUI
        # or prints usage. Do not execute it here.
        return "installed (version probe skipped for ODA GUI app)"
    except Exception as exc:  # never fatal for diagnostics
        return f"unknown ({exc.__class__.__name__}: {exc})"


def convert_dwg_to_dxf(
    dwg_path: Path,
    out_dxf_path: Path,
    converter: DwgConverter,
    timeout: int = 600,
) -> Path:
    """Convert one DWG file to DXF at ``out_dxf_path``. Raises DwgConvertError."""
    dwg_path = Path(dwg_path)
    out_dxf_path = Path(out_dxf_path)
    out_dxf_path.parent.mkdir(parents=True, exist_ok=True)
    if converter.kind == KIND_ODA:
        return _convert_with_oda(dwg_path, out_dxf_path, converter, timeout)
    return _convert_with_libredwg(dwg_path, out_dxf_path, converter, timeout)


def _convert_with_libredwg(
    dwg_path: Path, out_dxf_path: Path, converter: DwgConverter, timeout: int
) -> Path:
    if out_dxf_path.exists():
        out_dxf_path.unlink()
    cmd = [converter.path, "-y", "-o", str(out_dxf_path), str(dwg_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise DwgConvertError(f"dwg2dxf timed out after {timeout}s: {dwg_path}") from exc
    except OSError as exc:
        raise DwgConvertError(f"failed to run {converter.path}: {exc}") from exc
    if not out_dxf_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise DwgConvertError(
            f"dwg2dxf produced no output (exit {proc.returncode})"
            + (f": {detail}" if detail else "")
        )
    return out_dxf_path


def _convert_with_oda(
    dwg_path: Path, out_dxf_path: Path, converter: DwgConverter, timeout: int
) -> Path:
    # ezdxf's odafc add-on knows ODA's quirky directory-based CLI, but it
    # resolves the executable from ezdxf.options (odafc-addon win/unix_exec_path)
    # or PATH — it does NOT take a path argument. So push our detected/forced
    # converter.path into the options first, otherwise DWG2PDF_CONVERTER and our
    # own auto-detection would be silently ignored for the ODA backend.
    try:
        from ezdxf.addons import odafc
    except Exception as exc:
        raise DwgConvertError(f"ezdxf odafc add-on unavailable: {exc}") from exc

    option_key = "win_exec_path" if sys.platform == "win32" else "unix_exec_path"
    try:
        import ezdxf

        ezdxf.options.set("odafc-addon", option_key, converter.path)
    except Exception:
        # Older/odd ezdxf option layouts: fall back to the module attribute the
        # add-on also reads. Non-fatal — odafc will still try PATH.
        try:
            setattr(odafc, option_key, converter.path)
        except Exception:
            pass

    if out_dxf_path.exists():
        out_dxf_path.unlink()
    # LIMIT: odafc.convert() exposes no timeout; a hung ODA process blocks this
    # worker indefinitely (converter_timeout only guards the LibreDWG path).
    # Upgrade: drive ODAFileConverter via subprocess directly with timeout= when
    # users report ODA hangs — kept on odafc now for its correct dir-based CLI.
    try:
        odafc.convert(
            dwg_path,
            out_dxf_path,
            version="R2018",
            audit=True,
            replace=True,
        )
    except Exception as exc:
        raise DwgConvertError(f"ODA File Converter failed: {exc.__class__.__name__}: {exc}") from exc
    if not out_dxf_path.exists():
        raise DwgConvertError("ODA File Converter produced no output file")
    return out_dxf_path
