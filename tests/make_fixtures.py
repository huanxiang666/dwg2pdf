"""Regenerate the committed test fixtures in tests/data/.

Usage:  python tests/make_fixtures.py [--dxf2dwg /path/to/dxf2dwg]

Creates:
  tests/data/sample.dxf  — modelspace geometry + text, plus a paperspace
                           "Layout1" title block (so layout=auto picks it).
  tests/data/sample.dwg  — same drawing as DWG (needs LibreDWG dxf2dwg;
                           skipped with a warning when unavailable).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

DATA_DIR = Path(__file__).resolve().parent / "data"


def build_sample_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # millimeters

    msp = doc.modelspace()
    # Drawing frame + geometry in several colors (used by monochrome tests).
    msp.add_lwpolyline(
        [(0, 0), (200, 0), (200, 120), (0, 120)], close=True,
        dxfattribs={"color": 7},
    )
    msp.add_line((0, 0), (200, 120), dxfattribs={"color": 1})  # red
    msp.add_circle((100, 60), 40, dxfattribs={"color": 5})  # blue
    msp.add_arc(center=(40, 30), radius=15, start_angle=0, end_angle=180,
                dxfattribs={"color": 3})
    msp.add_lwpolyline(
        [(120, 20), (140, 50), (160, 25), (180, 60)],
        dxfattribs={"color": 2},
    )
    msp.add_text(
        "DWG2PDF SAMPLE", height=10,
        dxfattribs={"color": 7},
    ).set_placement((100, 100), align=TextEntityAlignment.MIDDLE_CENTER)

    # Paperspace title block so that layout=auto has something to select.
    psp = doc.paperspace("Layout1")
    psp.add_lwpolyline(
        [(0, 0), (287, 0), (287, 200), (0, 200)], close=True,
        dxfattribs={"color": 7},
    )
    psp.add_lwpolyline(
        [(180, 0), (180, 50), (287, 50)], dxfattribs={"color": 7}
    )
    psp.add_text(
        "TITLE BLOCK - Layout1", height=8,
        dxfattribs={"color": 7},
    ).set_placement((233, 25), align=TextEntityAlignment.MIDDLE_CENTER)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))
    print(f"wrote {path}")


def build_sample_dwg(dxf_path: Path, dwg_path: Path, dxf2dwg: str | None) -> bool:
    tool = dxf2dwg or shutil.which("dxf2dwg")
    if not tool:
        print("warning: dxf2dwg not found — sample.dwg not regenerated", file=sys.stderr)
        return False
    cmd = [tool, "-y", "-o", str(dwg_path), str(dxf_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not dwg_path.exists():
        print(f"warning: dxf2dwg failed (exit {proc.returncode}): "
              f"{(proc.stderr or proc.stdout)[-300:]}", file=sys.stderr)
        return False
    print(f"wrote {dwg_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dxf2dwg", default=None, help="path to LibreDWG dxf2dwg")
    args = parser.parse_args()

    dxf_path = DATA_DIR / "sample.dxf"
    dwg_path = DATA_DIR / "sample.dwg"
    build_sample_dxf(dxf_path)
    build_sample_dwg(dxf_path, dwg_path, args.dxf2dwg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
