"""Real DWG integration tests — require a dwg2dxf converter on the machine.

Skipped automatically when no converter is available (e.g. Windows CI without
ODA File Converter). Set DWG2PDF_TEST_FIXTURES=/path/to/dwg/dir to additionally
sweep a corpus of real-world DWG files (e.g. the LibreDWG test-data tree).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import find_tool, write_colored_dxf

from dwg2pdf.batch import convert_one
from dwg2pdf.cli import EXIT_OK, main
from dwg2pdf.dwg_backend import ENV_CONVERTER
from dwg2pdf.options import ConvertOptions

pytestmark = pytest.mark.integration


@pytest.fixture
def use_dwg2dxf(dwg2dxf_path, monkeypatch):
    monkeypatch.setenv(ENV_CONVERTER, dwg2dxf_path)
    return dwg2dxf_path


def test_sample_dwg_to_pdf_cli(tmp_path, sample_dwg, use_dwg2dxf):
    out = tmp_path / "pdf"
    code = main([str(sample_dwg), "-o", str(out), "-j", "1"])
    assert code == EXIT_OK
    pdfs = list(out.glob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].stat().st_size > 500
    assert pdfs[0].read_bytes()[:5] == b"%PDF-"


def test_dxf_to_dwg_roundtrip(tmp_path, dxf2dwg_path, use_dwg2dxf):
    """dxf -> dwg (dxf2dwg) -> pdf (our tool) proves the whole DWG chain."""
    src = write_colored_dxf(tmp_path / "round.dxf")
    dwg = tmp_path / "round.dwg"
    proc = subprocess.run(
        [dxf2dwg_path, "-y", "-o", str(dwg), str(src)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not dwg.exists():
        pytest.skip(f"dxf2dwg could not produce DWG: {(proc.stderr or proc.stdout)[-200:]}")
    res = convert_one(dwg, tmp_path / "round.pdf", ConvertOptions(layout="modelspace"))
    assert res.status == "ok", res.error
    assert res.pages == 1
    assert (tmp_path / "round.pdf").read_bytes()[:5] == b"%PDF-"


@pytest.mark.skipif(not os.environ.get("DWG2PDF_TEST_FIXTURES"), reason="corpus sweep only on demand")
def test_real_world_dwg_corpus_sweep(tmp_path, use_dwg2dxf):
    corpus = Path(os.environ["DWG2PDF_TEST_FIXTURES"])
    dwgs = sorted(corpus.rglob("*.dwg"))[:60]
    assert dwgs, f"no .dwg files under {corpus}"
    ok, failed = 0, []
    for dwg in dwgs:
        res = convert_one(dwg, tmp_path / (dwg.stem + ".pdf"), ConvertOptions(layout="auto"))
        if res.status == "ok":
            ok += 1
        else:
            failed.append((dwg.name, res.error[:120]))
    print(f"\ncorpus sweep: {ok}/{len(dwgs)} ok")
    for name, err in failed:
        print(f"  FAIL {name}: {err}")
    # LibreDWG's own test-data contains intentionally odd files; require a
    # solid majority to convert so real regressions still fail this test.
    assert ok >= len(dwgs) * 0.6
