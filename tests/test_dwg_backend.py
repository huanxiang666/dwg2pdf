"""DWG backend tests with fake converter executables (no real ODA needed)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from conftest import write_colored_dxf

from dwg2pdf.dwg_backend import (
    ENV_CONVERTER,
    KIND_LIBREDWG,
    KIND_ODA,
    DwgConvertError,
    DwgConverter,
    convert_dwg_to_dxf,
    detect_converter,
)


def _make_script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_dwg2dxf(tmp_path, monkeypatch):
    """A fake dwg2dxf that copies a valid DXF over the requested -o target."""
    fixture = write_colored_dxf(tmp_path / "fixture.dxf")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_script(
        bin_dir,
        "dwg2dxf",
        f'# fake dwg2dxf: expects -y -o OUT IN\nout="$3"\ncp "{fixture}" "$out"\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv(ENV_CONVERTER, raising=False)
    return bin_dir / "dwg2dxf"


def test_detect_libredwg_from_path(fake_dwg2dxf):
    converter = detect_converter()
    assert converter is not None
    assert converter.kind == KIND_LIBREDWG
    assert Path(converter.path).name == "dwg2dxf"


def test_detect_oda_from_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_script(bin_dir, "ODAFileConverter", "exit 0\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv(ENV_CONVERTER, raising=False)
    converter = detect_converter()
    assert converter is not None
    assert converter.kind == KIND_ODA


def test_env_var_overrides_and_infers_kind(tmp_path, monkeypatch, fake_dwg2dxf):
    monkeypatch.setenv(ENV_CONVERTER, str(fake_dwg2dxf))
    converter = detect_converter()
    assert converter is not None
    assert converter.kind == KIND_LIBREDWG
    assert converter.path == str(fake_dwg2dxf)


def test_env_var_broken_falls_back_to_path(tmp_path, monkeypatch, fake_dwg2dxf):
    monkeypatch.setenv(ENV_CONVERTER, str(tmp_path / "does-not-exist"))
    converter = detect_converter()
    assert converter is not None  # falls back to PATH detection
    assert converter.kind == KIND_LIBREDWG


def test_convert_with_fake_libredwg(tmp_path, fake_dwg2dxf):
    dwg = tmp_path / "in.dwg"
    dwg.write_bytes(b"fake dwg")
    out = tmp_path / "out.dxf"
    converter = DwgConverter(kind=KIND_LIBREDWG, path=str(fake_dwg2dxf))
    result = convert_dwg_to_dxf(dwg, out, converter, timeout=30)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_convert_failure_includes_stderr(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = _make_script(bin_dir, "dwg2dxf", 'echo "DWG version unsupported" >&2\nexit 3\n')
    converter = DwgConverter(kind=KIND_LIBREDWG, path=str(script))
    with pytest.raises(DwgConvertError) as excinfo:
        convert_dwg_to_dxf(tmp_path / "x.dwg", tmp_path / "x.dxf", converter, timeout=30)
    assert "unsupported" in str(excinfo.value)


def test_convert_timeout(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = _make_script(bin_dir, "dwg2dxf", "sleep 10\n")
    converter = DwgConverter(kind=KIND_LIBREDWG, path=str(script))
    with pytest.raises(DwgConvertError, match="timed out"):
        convert_dwg_to_dxf(tmp_path / "x.dwg", tmp_path / "x.dxf", converter, timeout=1)


def test_convert_missing_binary_reports_error(tmp_path):
    converter = DwgConverter(kind=KIND_LIBREDWG, path=str(tmp_path / "no-such-tool"))
    with pytest.raises(DwgConvertError):
        convert_dwg_to_dxf(tmp_path / "x.dwg", tmp_path / "x.dxf", converter, timeout=10)


def test_oda_path_surfaces_error_when_binary_broken(tmp_path):
    converter = DwgConverter(kind=KIND_ODA, path=str(tmp_path / "no-oda-here"))
    with pytest.raises(DwgConvertError):
        convert_dwg_to_dxf(tmp_path / "x.dwg", tmp_path / "x.dxf", converter, timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake scripts")
def test_no_converter_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir only
    monkeypatch.delenv(ENV_CONVERTER, raising=False)
    # ODA might still be really installed on dev machines; only assert the
    # libredwg branch is gone.
    converter = detect_converter()
    assert converter is None or converter.kind == KIND_ODA
