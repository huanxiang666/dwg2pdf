"""Batch layer tests: collection, target planning, results, parallel vs serial."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import write_colored_dxf, write_corrupt_dxf

from dwg2pdf.batch import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    collect_inputs,
    convert_one,
    plan_targets,
    run_batch,
)
from dwg2pdf.options import ConvertOptions


@pytest.fixture
def tree(tmp_path):
    """tmp/tree: a.dxf, b.dxf, notes.txt, sub/c.dxf, sub/deep/d.dxf"""
    root = tmp_path / "tree"
    (root / "sub" / "deep").mkdir(parents=True)
    write_colored_dxf(root / "a.dxf")
    write_colored_dxf(root / "b.dxf")
    write_colored_dxf(root / "sub" / "c.dxf")
    write_colored_dxf(root / "sub" / "deep" / "d.dxf")
    (root / "notes.txt").write_text("ignore me")
    return root


def test_collect_inputs_flat_and_recursive(tree):
    files, ignored = collect_inputs([str(tree)], recursive=False)
    names = [f.name for f in files]
    assert names == ["a.dxf", "b.dxf"]

    files, ignored = collect_inputs([str(tree)], recursive=True)
    names = [f.name for f in files]
    assert names == ["a.dxf", "b.dxf", "c.dxf", "d.dxf"]


def test_collect_inputs_dedups_and_ignores(tree, tmp_path):
    files, ignored = collect_inputs(
        [str(tree / "a.dxf"), str(tree / "a.dxf"), str(tree / "notes.txt"), str(tmp_path / "ghost.dxf")]
    )
    assert [f.name for f in files] == ["a.dxf"]
    assert any(p.name == "notes.txt" for p in ignored)
    assert any(p.name == "ghost.dxf" for p in ignored)


def test_plan_targets_collision_same_stem(tmp_path):
    a_dwg = tmp_path / "a.dwg"
    a_dxf = tmp_path / "a.dxf"
    a_dwg.touch()
    write_colored_dxf(a_dxf)
    plans = plan_targets([a_dwg, a_dxf], output_dir=None)
    targets = [t for _, t in plans]
    assert len({str(t).lower() for t in targets}) == 2, "a.dwg and a.dxf must not map to one PDF"
    assert all(t.suffix == ".pdf" for t in targets)


def test_plan_targets_flat_dir_collision(tmp_path):
    x = tmp_path / "x"
    y = tmp_path / "y"
    x.mkdir()
    y.mkdir()
    write_colored_dxf(x / "same.dxf")
    write_colored_dxf(y / "same.dxf")
    out = tmp_path / "out"
    plans = plan_targets([x / "same.dxf", y / "same.dxf"], output_dir=str(out))
    targets = {str(t).lower() for _, t in plans}
    assert len(targets) == 2
    # Lowercase both sides: on Windows the tmp path contains uppercase
    # (C:\Users\...) while targets are lowercased here for the distinctness
    # check — comparing a mixed-case needle to lowercase haystacks would
    # falsely fail. Path handling itself is exercised by the conversion tests.
    out_l = str(out).lower()
    assert all(out_l in t for t in targets)


def test_convert_one_ok_and_failed_and_skipped(tmp_path):
    src = write_colored_dxf(tmp_path / "ok.dxf")
    res = convert_one(src, tmp_path / "ok.pdf", ConvertOptions(layout="modelspace"))
    assert res.status == STATUS_OK
    assert res.pages == 1
    assert (tmp_path / "ok.pdf").exists()

    # skip_existing now skips
    res2 = convert_one(src, tmp_path / "ok.pdf", ConvertOptions(layout="modelspace", skip_existing=True))
    assert res2.status == STATUS_SKIPPED

    corrupt = write_corrupt_dxf(tmp_path / "bad.dxf")
    res3 = convert_one(corrupt, tmp_path / "bad.pdf", ConvertOptions())
    assert res3.status == STATUS_FAILED
    assert res3.error


def test_convert_one_missing_dwg_reports_clearly(tmp_path):
    ghost = tmp_path / "ghost.dwg"
    res = convert_one(ghost, tmp_path / "ghost.pdf", ConvertOptions())
    assert res.status == STATUS_FAILED
    assert res.error


def test_large_drawing_warning(tmp_path):
    """A DXF above large_dxf_mb must produce an explicit slow-render warning
    (regression for the '相岙水库.dwg looks hung' user report)."""
    src = write_colored_dxf(tmp_path / "big.dxf")
    opts = ConvertOptions(layout="modelspace", large_dxf_mb=0.0001)  # tiny threshold
    res = convert_one(src, tmp_path / "big.pdf", opts)
    assert res.status == STATUS_OK
    assert any("large drawing" in w for w in res.warnings)
    # default threshold (5 MB) → no warning for the tiny fixture
    res2 = convert_one(src, tmp_path / "big2.pdf", ConvertOptions(layout="modelspace"))
    assert res2.status == STATUS_OK
    assert not any("large drawing" in w for w in res2.warnings)


def _make_batch_inputs(tmp_path, count=4, corrupt_index=2):
    paths = []
    for i in range(count):
        p = tmp_path / f"f{i}.dxf"
        if i == corrupt_index:
            write_corrupt_dxf(p)
        else:
            write_colored_dxf(p)
        paths.append(p)
    return paths


@pytest.mark.parametrize("jobs", [1, 2])
def test_run_batch_mixed_results(tmp_path, jobs):
    paths = _make_batch_inputs(tmp_path, count=4, corrupt_index=2)
    plans = plan_targets(paths, output_dir=str(tmp_path / "out"))
    batch = run_batch(plans, ConvertOptions(layout="modelspace", jobs=jobs))
    assert len(batch.results) == 4
    assert len(batch.ok) == 3
    assert len(batch.failed) == 1
    # results keep input order regardless of jobs/completion order
    assert [Path(r.source).name for r in batch.results] == ["f0.dxf", "f1.dxf", "f2.dxf", "f3.dxf"]
    assert batch.failed[0].error
    for r in batch.ok:
        assert Path(r.target).exists()


def test_run_batch_progress_callback(tmp_path):
    paths = _make_batch_inputs(tmp_path, count=3, corrupt_index=-1)
    plans = plan_targets(paths, output_dir=str(tmp_path / "out"))
    seen = []
    run_batch(plans, ConvertOptions(layout="modelspace", jobs=1), progress_cb=lambda r, d, t: seen.append((d, t)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_batch_report_structure(tmp_path):
    paths = _make_batch_inputs(tmp_path, count=2, corrupt_index=-1)
    plans = plan_targets(paths, output_dir=str(tmp_path / "out"))
    opts = ConvertOptions(layout="modelspace", jobs=1)
    batch = run_batch(plans, opts)
    report = batch.to_report(opts)
    assert report["totals"]["files"] == 2
    assert report["totals"]["ok"] == 2
    assert len(report["results"]) == 2
    assert {"source", "target", "status", "pages"} <= set(report["results"][0])
