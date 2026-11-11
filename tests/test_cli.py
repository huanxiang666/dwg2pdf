"""CLI end-to-end tests (in-process main() calls)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from conftest import write_colored_dxf, write_corrupt_dxf

from dwg2pdf.cli import EXIT_OK, EXIT_SOME_FAILED, EXIT_USAGE, _ensure_utf8_stdio, main


def test_cli_converts_single_file(tmp_path, sample_dxf, capsys):
    out = tmp_path / "pdf"
    code = main([str(sample_dxf), "-o", str(out), "-j", "1"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    pdfs = list(out.glob("*.pdf"))
    assert len(pdfs) == 1
    assert "Done" in captured.out


def test_cli_report_json(tmp_path, sample_dxf):
    out = tmp_path / "pdf"
    report = tmp_path / "report.json"
    code = main([str(sample_dxf), "-o", str(out), "-j", "1", "--report", str(report)])
    assert code == EXIT_OK
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["ok"] == 1
    assert data["results"][0]["pages"] >= 1
    assert data["options"]["layout"] == "auto"


def test_cli_partial_failure_exit_code(tmp_path, capsys):
    good = write_colored_dxf(tmp_path / "good.dxf")
    bad = write_corrupt_dxf(tmp_path / "bad.dxf")
    out = tmp_path / "pdf"
    code = main([str(good), str(bad), "-o", str(out), "-j", "1"])
    captured = capsys.readouterr()
    assert code == EXIT_SOME_FAILED
    assert (out / "good.pdf").exists()
    assert not (out / "bad.pdf").exists()
    assert "Failed files" in captured.err


def test_cli_no_inputs_is_usage_error(tmp_path, capsys):
    code = main([])
    assert code == EXIT_USAGE


def test_cli_directory_without_cad_files(tmp_path, capsys):
    (tmp_path / "readme.md").write_text("hi")
    code = main([str(tmp_path)])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "no .dwg/.dxf" in captured.err.lower() or "error" in captured.err.lower()


def test_cli_invalid_option(tmp_path, capsys):
    code = main(["whatever.dxf", "--paper", "B7", "--scale", "-1"])
    assert code == EXIT_USAGE


def test_cli_check_runs(capsys):
    code = main(["--check"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "dwg2pdf" in captured.out
    assert "ezdxf" in captured.out


def test_cli_options_smoke(tmp_path, sample_dxf):
    """Monochrome + named paper + scale through the full CLI path."""
    out = tmp_path / "pdf"
    code = main([
        str(sample_dxf), "-o", str(out), "-j", "1",
        "--layout", "modelspace", "--monochrome", "--paper", "a3", "--scale", "0.5",
        "--margin", "10", "--fit",
    ])
    assert code == EXIT_OK
    assert len(list(out.glob("*.pdf"))) == 1


def test_cli_recursive_directory(tmp_path, capsys):
    root = tmp_path / "drawings"
    (root / "sub").mkdir(parents=True)
    write_colored_dxf(root / "a.dxf")
    write_colored_dxf(root / "sub" / "b.dxf")
    out = tmp_path / "pdf"
    code = main([str(root), "-r", "-o", str(out), "-j", "1"])
    assert code == EXIT_OK
    assert len(list(out.glob("*.pdf"))) == 2


def test_cli_skip_existing(tmp_path, capsys):
    src = write_colored_dxf(tmp_path / "a.dxf")
    out = tmp_path / "pdf"
    assert main([str(src), "-o", str(out), "-j", "1"]) == EXIT_OK
    capsys.readouterr()
    assert main([str(src), "-o", str(out), "-j", "1", "--skip-existing"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "skipped" in captured.out.lower() or "SKIP" in captured.out


def test_ensure_utf8_stdio_survives_windows_codepage(monkeypatch):
    """Regression: on Windows a piped console uses cp1252; printing the CJK
    install hint used to raise UnicodeEncodeError and abort the run. After
    _ensure_utf8_stdio the same write must succeed and round-trip as UTF-8."""
    buf = io.BytesIO()
    # cp1252 + strict reproduces the Windows console exactly: CJK -> raise.
    fake = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    # Sanity: without the fix this write genuinely fails on cp1252.
    with pytest.raises(UnicodeEncodeError):
        fake.write("没有找到 DWG 转换器")
        fake.flush()
    monkeypatch.setattr(sys, "stdout", fake)
    _ensure_utf8_stdio()
    sys.stdout.write("没有找到 DWG 转换器 ODA File Converter")
    sys.stdout.flush()
    assert "转换器" in buf.getvalue().decode("utf-8")


def test_check_does_not_crash_on_windows_console(tmp_path, monkeypatch):
    """--check emits the bilingual install hint; it must not raise under a
    non-UTF-8 stdout. Exit code stays 0 regardless of converter presence."""
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp1252", errors="strict"))
    code = main(["--check"])
    sys.stdout.flush()
    assert code == EXIT_OK
    # Something was written and it decodes as UTF-8 (the reconfigured stream).
    assert b"dwg2pdf" in buf.getvalue()


def test_heartbeat_emits_periodically():
    """The long-render heartbeat must actually emit progress lines."""
    import threading
    import time as _time

    from dwg2pdf.cli import _heartbeat_loop

    state = {"done": 1, "total": 5, "start": _time.monotonic()}
    stop = threading.Event()
    lines: list[str] = []
    t = threading.Thread(
        target=_heartbeat_loop, args=(state, stop, 0.03, lines.append), daemon=True
    )
    t.start()
    _time.sleep(0.15)
    stop.set()
    t.join(timeout=2)
    assert len(lines) >= 2, f"expected periodic heartbeats, got {lines}"
    assert "[heartbeat]" in lines[0] and "1/5" in lines[0]
