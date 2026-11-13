"""Optional Go fast-render core.

This repository publishes a second, independent release train: a Go rewrite
of the DXF->PDF renderer (single static binary, DXF parsing ~190 ms for a
38.5 MB file, searchable text output). It is OPTIONAL — the Python renderer
remains the default and the fallback.

Facts verified against v1.1.1 (linux-amd64):
- flags must precede positional args (Go flag package):
    dwg2pdf -o OUTDIR -j N [-paper P] [-monochrome] [-margin MM] FILE...
- DWG input needs an external dwg2dxf just like the Python renderer, so we
  always hand the Go core a DXF (converting DWG ourselves via the detected
  engine first).
- no layout selection flag; no curve-quality flags. When the user picks the
  Go core with layout != auto we surface a warning rather than pretend the
  option applies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def find_go_core() -> str | None:
    """Locate the downloaded Go core binary, or None."""
    env = os.environ.get("DWG2PDF_GO_CORE", "").strip()
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    try:
        from .engine_download import user_engine_dir

        exe = "dwg2pdf-go.exe" if sys.platform == "win32" else "dwg2pdf-go"
        cand = user_engine_dir() / "go-core" / exe
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    except Exception:
        pass
    return None


def go_core_version(go_bin: str) -> str:
    try:
        proc = subprocess.run([go_bin, "-version"], capture_output=True,
                              text=True, timeout=15)
        return (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr) else "unknown"
    except Exception:
        return "unknown"


def convert_with_go(
    dxf_path: Path | str,
    target_pdf: Path | str,
    opts,
    go_bin: str | None = None,
    timeout: int = 3600,
) -> dict:
    """Render one DXF through the Go core onto target_pdf.

    Returns {"seconds": float, "pages": int|None}. Raises RuntimeError with a
    readable message on any failure so callers can fall back to the Python
    renderer.
    """
    go_bin = go_bin or find_go_core()
    if not go_bin:
        raise RuntimeError("Go 核心未安装（引擎助手里可下载）")
    dxf_path = Path(dxf_path)
    target_pdf = Path(target_pdf)
    target_pdf.parent.mkdir(parents=True, exist_ok=True)

    cmd = [go_bin, "-o", str(target_pdf.parent), "-j", str(max(1, int(getattr(opts, "threads", 0) or os.cpu_count() or 1)))]
    if getattr(opts, "paper", "auto") != "auto":
        cmd += ["-paper", opts.paper]
    if getattr(opts, "monochrome", False):
        cmd.append("-monochrome")
    if getattr(opts, "margin", None) is not None:
        cmd += ["-margin", str(opts.margin)]
    if float(getattr(opts, "scale", 1.0) or 1.0) != 1.0:
        cmd += ["-scale", str(opts.scale)]
    for d in getattr(opts, "font_dirs", ()) or ():
        cmd += ["-font-dir", str(d)]

    report = None
    t0 = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as rf:
            report = rf.name
        cmd += ["-report", report, str(dxf_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Go 核心超时（{timeout}s）") from exc
    finally:
        pass
    seconds = time.perf_counter() - t0

    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"Go 核心失败（exit {proc.returncode}）: {out.strip()[:300]}")

    # The Go core writes <stem>.pdf into -o dir; our target may carry a
    # collision suffix — move the produced file onto the exact target.
    produced = target_pdf.parent / (dxf_path.stem + ".pdf")
    if produced != target_pdf:
        if not produced.is_file():
            raise RuntimeError(f"Go 核心没有产出 {produced.name}")
        os.replace(produced, target_pdf)
    if not target_pdf.is_file() or target_pdf.stat().st_size == 0:
        raise RuntimeError("Go 核心产出了空文件")
    if not target_pdf.read_bytes()[:5] == b"%PDF-":
        raise RuntimeError("Go 核心产出不是有效 PDF")

    pages = None
    if report and Path(report).exists():
        try:
            data = json.loads(Path(report).read_text(encoding="utf-8", errors="replace"))
            results = data.get("results") or []
            if results:
                pages = results[0].get("pages")
        except Exception:
            pages = None
        try:
            os.unlink(report)
        except OSError:
            pass
    return {"seconds": round(seconds, 3), "pages": pages}
