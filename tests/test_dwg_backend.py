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
    _infer_kind,
    convert_dwg_to_dxf,
    detect_converter,
)

# These tests fabricate a POSIX `#!/bin/sh` converter and either execute it or
# rely on shutil.which() finding an extension-less file on PATH. Neither works
# on Windows (CreateProcess rejects sh scripts: WinError 193; shutil.which only
# tries PATHEXT extensions). Backend *logic* stays covered cross-platform by
# test_infer_kind_* and the env-var/missing-binary tests below, and end-to-end
# by the release-workflow frozen-exe smoke test on windows-latest.
posix_script = pytest.mark.skipif(
    sys.platform == "win32",
    reason="fake POSIX sh converter cannot run / be found on PATH under Windows",
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


@posix_script
def test_detect_libredwg_from_path(fake_dwg2dxf):
    converter = detect_converter()
    assert converter is not None
    assert converter.kind == KIND_LIBREDWG
    assert Path(converter.path).name == "dwg2dxf"


@posix_script
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


@posix_script
def test_env_var_broken_falls_back_to_path(tmp_path, monkeypatch, fake_dwg2dxf):
    monkeypatch.setenv(ENV_CONVERTER, str(tmp_path / "does-not-exist"))
    converter = detect_converter()
    assert converter is not None  # falls back to PATH detection
    assert converter.kind == KIND_LIBREDWG


@posix_script
def test_convert_with_fake_libredwg(tmp_path, fake_dwg2dxf):
    dwg = tmp_path / "in.dwg"
    dwg.write_bytes(b"fake dwg")
    out = tmp_path / "out.dxf"
    converter = DwgConverter(kind=KIND_LIBREDWG, path=str(fake_dwg2dxf))
    result = convert_dwg_to_dxf(dwg, out, converter, timeout=30)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


@posix_script
def test_convert_failure_includes_stderr(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = _make_script(bin_dir, "dwg2dxf", 'echo "DWG version unsupported" >&2\nexit 3\n')
    converter = DwgConverter(kind=KIND_LIBREDWG, path=str(script))
    with pytest.raises(DwgConvertError) as excinfo:
        convert_dwg_to_dxf(tmp_path / "x.dwg", tmp_path / "x.dxf", converter, timeout=30)
    assert "unsupported" in str(excinfo.value)


@posix_script
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


def test_oda_converter_path_is_pushed_into_ezdxf_options(tmp_path, monkeypatch):
    """Regression (blocking #4): DWG2PDF_CONVERTER / detected ODA path must be
    written into ezdxf.options before odafc.convert runs, otherwise odafc
    resolves its own executable from PATH and our forced path is ignored."""
    import ezdxf
    from ezdxf.addons import odafc

    sentinel = str(tmp_path / "my-ODAFileConverter")
    option_key = "win_exec_path" if sys.platform == "win32" else "unix_exec_path"
    captured = {}

    def fake_convert(source, dest="", **kwargs):
        # Record what ezdxf.options points at the moment odafc would exec.
        captured["option"] = ezdxf.options.get("odafc-addon", option_key)
        Path(dest).write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")

    monkeypatch.setattr(odafc, "convert", fake_convert)
    converter = DwgConverter(kind=KIND_ODA, path=sentinel)
    convert_dwg_to_dxf(tmp_path / "in.dwg", tmp_path / "out.dxf", converter, timeout=10)
    assert captured.get("option") == sentinel


def test_infer_kind_is_substring_based(tmp_path):
    """A user-named converter still routes correctly by basename substring."""
    assert _infer_kind(str(tmp_path / "weird-dwg2dxf-wrapper")) == KIND_LIBREDWG
    assert _infer_kind(str(tmp_path / "ODAFileConverter")) == KIND_ODA


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake scripts")
def test_no_converter_returns_none(tmp_path, monkeypatch):
    import dwg2pdf.dwg_backend as db

    # Isolate from a locally-built bundled engine (src/dwg2pdf/engine/ is a
    # gitignored release-workflow artifact; it must not affect this test).
    monkeypatch.setattr(db, "bundled_dwg2dxf", lambda: None)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "nothing"), raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir only
    monkeypatch.delenv(ENV_CONVERTER, raising=False)
    # ODA might still be really installed on dev machines; only assert the
    # libredwg branch is gone.
    converter = detect_converter()
    assert converter is None or converter.kind == KIND_ODA


# Pure string logic — runs identically on Windows and POSIX, so kind
# inference stays covered even where the fake-script tests are skipped.
@pytest.mark.parametrize(
    "path,expected",
    [
        ("/usr/bin/dwg2dxf", KIND_LIBREDWG),
        ("C:\\Program Files\\LibreDWG\\dwg2dxf.exe", KIND_LIBREDWG),
        ("/opt/oda/dwg2dxf", KIND_LIBREDWG),
        ("C:\\Program Files\\ODA\\ODAFileConverter25.12\\ODAFileConverter.exe", KIND_ODA),
        ("/usr/local/bin/ODAFileConverter", KIND_ODA),
        # Unknown names default to the ODA directory-CLI convention.
        ("/opt/custom/some-converter", KIND_ODA),
    ],
)
def test_infer_kind(path, expected):
    assert _infer_kind(path) == expected


def _make_bundled_engine(tmp_path, monkeypatch):
    """Fake a PyInstaller bundle containing dwg2pdf/engine/dwg2dxf."""
    root = tmp_path / "meipass" / "dwg2pdf" / "engine"
    root.mkdir(parents=True)
    exe = root / "dwg2dxf"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    return exe


@posix_script
def test_bundled_engine_discovered(tmp_path, monkeypatch):
    from dwg2pdf.dwg_backend import bundled_dwg2dxf

    exe = _make_bundled_engine(tmp_path, monkeypatch)
    assert bundled_dwg2dxf() == str(exe)


@posix_script
def test_bundled_engine_wins_over_system_path(tmp_path, monkeypatch):
    """Bundled engine is version-pinned, so it outranks a random PATH dwg2dxf."""
    exe = _make_bundled_engine(tmp_path, monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_script(bin_dir, "dwg2dxf", "exit 0\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv(ENV_CONVERTER, raising=False)
    conv = detect_converter()
    assert conv is not None and conv.kind == KIND_LIBREDWG
    assert conv.path == str(exe)


def test_no_bundled_engine_returns_none(tmp_path, monkeypatch):
    from dwg2pdf.dwg_backend import bundled_dwg2dxf

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "nothing"), raising=False)
    # __file__-relative engine dir does not exist in the source tree by default
    assert bundled_dwg2dxf() is None or Path(bundled_dwg2dxf()).name == "dwg2dxf"
