"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

import ezdxf
import pytest

DATA_DIR = Path(__file__).resolve().parent / "data"

# Local source-build of LibreDWG (used on the dev machine, harmless elsewhere).
_EXTRA_TOOL_DIRS = [
    "/tmp/dwg2pdf-build/install/bin",
]


def find_tool(name: str) -> str | None:
    """Locate a LibreDWG tool on PATH or in well-known local build dirs."""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_TOOL_DIRS:
        cand = Path(d) / name
        if cand.is_file():
            return str(cand)
    return None


@pytest.fixture(scope="session")
def sample_dxf() -> Path:
    path = DATA_DIR / "sample.dxf"
    if not path.exists():
        pytest.skip("tests/data/sample.dxf missing — run tests/make_fixtures.py")
    return path


@pytest.fixture(scope="session")
def sample_dwg() -> Path:
    path = DATA_DIR / "sample.dwg"
    if not path.exists():
        pytest.skip("tests/data/sample.dwg missing — regenerate with tests/make_fixtures.py")
    return path


@pytest.fixture
def dwg2dxf_path() -> str:
    tool = find_tool("dwg2dxf")
    if not tool:
        pytest.skip("dwg2dxf not available")
    return tool


@pytest.fixture
def dxf2dwg_path() -> str:
    tool = find_tool("dxf2dwg")
    if not tool:
        pytest.skip("dxf2dwg not available")
    return tool


def write_colored_dxf(path: Path) -> Path:
    """Minimal DXF: one red line + one blue circle in modelspace only.

    setup=True matters: ezdxf's bare minimal documents trip a duplicate-handle
    bug in LibreDWG's dxf2dwg, which makes generated DWGs unreadable after a
    dwg2dxf round trip (observed with LibreDWG 0.14).
    """
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # millimeters
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 50), dxfattribs={"color": 1})  # red (ACI 1)
    msp.add_circle((50, 25), 20, dxfattribs={"color": 5})  # blue (ACI 5)
    doc.saveas(str(path))
    return path


def write_corrupt_dxf(path: Path) -> Path:
    path.write_bytes(b"this is definitely not a DXF file\x00\xff\xfe")
    return path
