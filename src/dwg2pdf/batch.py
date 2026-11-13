"""Batch conversion: input collection, target planning, parallel execution.

All worker-facing functions are module level and picklable. Each file is
converted independently; one failure never aborts the batch.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

from . import SUPPORTED_INPUT_EXTENSIONS, __version__
from .dwg_backend import (
    INSTALL_HINT,
    DwgConvertError,
    convert_dwg_to_dxf,
    detect_converter,
)
from .options import ConvertOptions
from .renderer import RenderError, render_dxf_to_pdf, setup_font_dirs

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass
class FileResult:
    """Outcome for one input file (picklable worker -> parent message)."""

    source: str
    target: str = ""
    status: str = STATUS_FAILED
    error: str = ""
    pages: int = 0
    layouts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


@dataclass
class BatchResult:
    """Aggregated outcome of a batch run."""

    results: list[FileResult] = field(default_factory=list)
    total_seconds: float = 0.0

    @property
    def ok(self) -> list[FileResult]:
        return [r for r in self.results if r.status == STATUS_OK]

    @property
    def failed(self) -> list[FileResult]:
        return [r for r in self.results if r.status == STATUS_FAILED]

    @property
    def skipped(self) -> list[FileResult]:
        return [r for r in self.results if r.status == STATUS_SKIPPED]

    def to_report(self, opts: ConvertOptions) -> dict:
        return {
            "tool": f"dwg2pdf {__version__}",
            "totals": {
                "files": len(self.results),
                "ok": len(self.ok),
                "failed": len(self.failed),
                "skipped": len(self.skipped),
                "seconds": round(self.total_seconds, 3),
            },
            "options": {
                "layout": opts.layout,
                "monochrome": opts.monochrome,
                "paper": opts.paper,
                "scale": opts.scale,
                "margin": opts.margin,
                "fit": opts.fit,
                "output_dir": opts.output_dir,
                "skip_existing": opts.skip_existing,
            },
            "results": [
                {
                    "source": r.source,
                    "target": r.target,
                    "status": r.status,
                    "error": r.error,
                    "pages": r.pages,
                    "layouts": r.layouts,
                    "warnings": r.warnings,
                    "seconds": round(r.seconds, 3),
                }
                for r in self.results
            ],
        }


def collect_inputs(
    paths: list[str] | tuple[str, ...], recursive: bool = False
) -> tuple[list[Path], list[Path]]:
    """Expand files/directories into a sorted, deduplicated list of CAD files.

    Returns (supported_files, ignored_paths). Ignored entries are files with
    unsupported extensions or paths that do not exist.
    """
    files: list[Path] = []
    ignored: list[Path] = []
    seen: set[str] = set()

    def add_file(p: Path) -> None:
        # normcase: case-insensitive dedup on Windows, case-preserving on
        # POSIX — so Part.dxf and part.dxf stay distinct on Linux (a plain
        # .lower() key would silently drop one of them there).
        key = os.path.normcase(str(p.resolve()))
        if key in seen:
            return
        seen.add(key)
        if p.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS:
            files.append(p)
        else:
            ignored.append(p)

    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            entries = sorted(p.glob(pattern))
            found_any = False
            for entry in entries:
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS:
                    add_file(entry)
                    found_any = True
            if not found_any:
                ignored.append(p)
        elif p.is_file():
            add_file(p)
        else:
            ignored.append(p)
    files.sort(key=lambda p: str(p).lower())
    return files, ignored


def plan_targets(files: list[Path], output_dir: str | None) -> list[tuple[Path, Path]]:
    """Map every source file to its PDF target, resolving name collisions.

    Without ``output_dir`` each PDF lands next to its source. Collisions (e.g.
    ``a.dwg`` + ``a.dxf``, or two ``a.dwg`` in different folders flattened into
    one output dir) are resolved by appending the source extension, then the
    parent folder name, then a numeric suffix.
    """
    plans: list[tuple[Path, Path]] = []
    used: dict[str, Path] = {}

    def claim(target: Path, src: Path) -> Path:
        existing = used.get(str(target).lower())
        if existing is None or existing == src:
            used[str(target).lower()] = src
            return target
        stem = target.stem
        candidates = [
            target.with_name(f"{stem}-{src.suffix.lstrip('.').lower()}.pdf"),
            target.with_name(f"{stem}-{src.parent.name}.pdf"),
        ]
        n = 2
        while True:
            candidates.append(target.with_name(f"{stem}-{n}.pdf"))
            candidate = candidates.pop(0)
            key = str(candidate).lower()
            if key not in used:
                used[key] = src
                return candidate
            n += 1

    for src in files:
        if output_dir:
            target = Path(output_dir).expanduser() / (src.stem + ".pdf")
        else:
            target = src.with_suffix(".pdf")
        plans.append((src, claim(target, src)))
    return plans


def _worker_init(opts: ConvertOptions) -> None:
    setup_font_dirs(opts.font_dirs)


def _large_drawing_note(dxf_path: Path, opts: ConvertOptions) -> str | None:
    """Size-based heads-up that rendering may take minutes.

    The render cost scales with recorded path count (measured: ~57 MB DXF ≈
    3 min modelspace on a 2020-class desktop; layout pages with viewports
    re-render the model per viewport and can take far longer). We cannot know
    the exact cost cheaply, so the intermediate DXF size is the proxy.
    """
    try:
        size_mb = dxf_path.stat().st_size / 1e6
    except OSError:
        return None
    if size_mb >= opts.large_dxf_mb:
        return (
            f"large drawing ({size_mb:.0f} MB DXF): rendering can take minutes; "
            "if this DWG has layout pages with viewports, '--layout modelspace' "
            "is usually much faster"
        )
    return None


def _render_dxf(dxf_path: Path, target: Path, opts: ConvertOptions,
                result: "FileResult", progress) -> "RenderResultLike":
    """Render one DXF with the selected core; Go core falls back to Python.

    Returns the render stats object (RenderResult for Python, a shim for Go).
    Appends warnings to ``result`` when a fallback happens or when options
    are ignored by the Go core.
    """
    if opts.core == "go":
        from . import go_core

        if go_core.find_go_core() is None:
            result.warnings.append(
                "core=go 但未找到 Go 核心，已用内置渲染器（引擎助手里可下载）"
            )
        else:
            if opts.layout not in ("auto", ""):
                result.warnings.append(
                    f"Go 核心不支持 layout={opts.layout}，按其默认布局输出"
                )
            try:
                info = go_core.convert_with_go(dxf_path, target, opts)
                return _GoRenderResult(info)
            except Exception as exc:
                result.warnings.append(
                    f"Go 核心失败，已回退内置渲染器：{exc}"
                )
    return render_dxf_to_pdf(dxf_path, target, opts, progress=progress)


class _GoRenderResult:
    """RenderResult-compatible shim for a successful Go-core conversion."""

    def __init__(self, info: dict):
        self.pages = info.get("pages") or 1
        self.layouts: list[str] = []
        self.warnings: list[str] = []
        self.seconds = info.get("seconds", 0.0)


def convert_one(
    source: str | Path,
    target: str | Path,
    opts: ConvertOptions,
    progress=None,
) -> FileResult:
    """Convert a single file. Never raises — all failures become FileResult.

    ``progress(done_chunks, total_chunks)`` is forwarded to the renderer for
    chunk-level progress on big layouts (serial mode only; pool workers have
    no channel back and chunk rendering there is forced serial anyway).
    """
    source = Path(source)
    target = Path(target)
    started = time.perf_counter()
    result = FileResult(source=str(source), target=str(target))
    try:
        if opts.skip_existing and target.exists():
            result.status = STATUS_SKIPPED
            result.seconds = time.perf_counter() - started
            return result

        note: str | None = None
        if source.suffix.lower() == ".dwg":
            converter = detect_converter()
            if converter is None:
                raise DwgConvertError(INSTALL_HINT.replace("\n", " "))
            import tempfile

            with tempfile.TemporaryDirectory(prefix="dwg2pdf-") as tmp_dir:
                tmp_dxf = Path(tmp_dir) / (source.stem + ".dxf")
                convert_dwg_to_dxf(
                    source, tmp_dxf, converter, timeout=opts.converter_timeout
                )
                note = _large_drawing_note(tmp_dxf, opts)
                if note:
                    print(f"[warn] {source.name}: {note}", file=sys.stderr, flush=True)
                    result.warnings.append(note)
                render = _render_dxf(tmp_dxf, target, opts, result, progress)
        else:
            note = _large_drawing_note(source, opts)
            if note:
                print(f"[warn] {source.name}: {note}", file=sys.stderr, flush=True)
                result.warnings.append(note)
            render = _render_dxf(source, target, opts, result, progress)

        result.status = STATUS_OK
        result.pages = render.pages
        result.layouts = render.layouts
        result.warnings.extend(render.warnings)
    except (RenderError, DwgConvertError) as exc:
        result.status = STATUS_FAILED
        result.error = str(exc)
    except Exception as exc:  # keep the batch alive no matter what
        result.status = STATUS_FAILED
        result.error = f"{exc.__class__.__name__}: {exc}"
    finally:
        result.seconds = time.perf_counter() - started
    return result


def _effective_jobs(opts: ConvertOptions, file_count: int) -> int:
    if opts.jobs > 0:
        jobs = opts.jobs
    else:
        jobs = os.cpu_count() or 1
    return max(1, min(jobs, file_count))


def run_batch(
    plans: list[tuple[Path, Path]],
    opts: ConvertOptions,
    progress_cb=None,
    chunk_progress_cb=None,
) -> BatchResult:
    """Convert all planned files; call ``progress_cb(FileResult, done, total)``.

    ``chunk_progress_cb(source_name, done_chunks, total_chunks)`` (optional)
    reports intra-file chunked-render progress in serial mode.

    Uses a process pool for >1 file (rendering is CPU-bound and PyMuPDF is not
    fork-friendly across threads). Falls back to serial execution when the pool
    cannot start.
    """
    batch = BatchResult()
    total = len(plans)
    if total == 0:
        return batch
    t0 = time.perf_counter()
    jobs = _effective_jobs(opts, total)

    def report(res: FileResult) -> None:
        batch.results.append(res)
        if progress_cb is not None:
            progress_cb(res, len(batch.results), total)

    if jobs == 1:
        _worker_init(opts)
        for src, dst in plans:
            chunk_cb = None
            if chunk_progress_cb is not None:
                chunk_cb = lambda done, total, _n=src.name: chunk_progress_cb(_n, done, total)
            report(convert_one(src, dst, opts, progress=chunk_cb))
    else:
        try:
            with ProcessPoolExecutor(
                max_workers=jobs, initializer=_worker_init, initargs=(opts,)
            ) as pool:
                future_map = {
                    pool.submit(convert_one, str(src), str(dst), opts): (src, dst)
                    for src, dst in plans
                }
                for future in as_completed(future_map):
                    src, dst = future_map[future]
                    try:
                        report(future.result())
                    except Exception as exc:
                        # Worker process died (crash/OOM): record it, keep going
                        # with whatever the pool can still deliver.
                        report(
                            FileResult(
                                source=str(src),
                                target=str(dst),
                                status=STATUS_FAILED,
                                error=f"worker crashed: {exc.__class__.__name__}: {exc}",
                            )
                        )
        except (OSError, NotImplementedError, BrokenProcessPool) as exc:
            # The pool could not start (restricted env: OSError/
            # NotImplementedError) or died mid-run (a worker was OOM-killed or
            # segfaulted: BrokenProcessPool). Either way, fall back to serial
            # for every file not yet reported so the batch still completes and
            # the user gets results instead of a bare traceback.
            already = {r.source for r in batch.results}
            _worker_init(opts)
            for src, dst in plans:
                if str(src) in already:
                    continue
                report(convert_one(src, dst, opts))
            print(
                f"[warn] process pool unavailable ({exc.__class__.__name__}: {exc}); "
                "finished the remaining files serially",
                file=sys.stderr,
            )
    batch.total_seconds = time.perf_counter() - t0
    # Keep input order in the report regardless of completion order.
    order = {str(src): i for i, (src, _) in enumerate(plans)}
    batch.results.sort(key=lambda r: order.get(r.source, 1 << 30))
    return batch
