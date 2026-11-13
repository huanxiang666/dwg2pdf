"""Command line interface for dwg2pdf."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .batch import collect_inputs, plan_targets, run_batch
from .options import PAPER_CHOICES, ConvertOptions, OptionError

EXIT_OK = 0
EXIT_SOME_FAILED = 1
EXIT_USAGE = 2


def _ensure_utf8_stdio() -> None:
    """Make stdout/stderr emit non-ASCII (Chinese) text without crashing.

    On Windows a redirected/piped console defaults to the locale codepage
    (e.g. cp1252); printing CJK hints such as the DWG-converter install guide
    then raises UnicodeEncodeError and aborts the run. Reconfigure any
    non-UTF-8 stream to UTF-8 with a replacement fallback. Streams already in
    UTF-8 (Linux, macOS, pytest capture) are left untouched.
    """
    import io

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if "utf8" in encoding:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            continue
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            try:
                setattr(
                    sys,
                    name,
                    io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"),
                )
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dwg2pdf",
        description=(
            "Batch convert CAD drawings (DWG/DXF) to vector PDF. "
            "DWG input needs ODA File Converter or LibreDWG dwg2dxf; "
            "DXF input works out of the box. "
            "DWG/DXF 图纸批量转 PDF；DWG 需安装转换器（--check 查看环境），DXF 开箱即用。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  dwg2pdf drawing.dwg                     # PDF next to the source\n"
            "  dwg2pdf ./drawings -r -o ./pdf            # whole tree into ./pdf\n"
            "  dwg2pdf a.dwg b.dxf --monochrome --paper a4\n"
            "  dwg2pdf --gui                             # graphical interface\n"
            "  dwg2pdf --check                           # show environment status\n"
        ),
    )
    parser.add_argument("inputs", nargs="*", help="input .dwg/.dxf files and/or directories")
    parser.add_argument("-o", "--output-dir", default=None, help="write all PDFs into this directory (default: next to each source)")
    parser.add_argument("-r", "--recursive", action="store_true", help="scan input directories recursively")
    parser.add_argument("--layout", default="auto", help="layout to export: auto (default), modelspace, all, or an exact layout name like Layout1")
    parser.add_argument("--monochrome", action="store_true", help="black-on-white output (print friendly)")
    parser.add_argument("--paper", default="auto", help="page size: auto (default) or one of " + ", ".join(PAPER_CHOICES[1:]) + " (aliases like a4 accepted)")
    parser.add_argument("--scale", type=float, default=1.0, help="extra content scale factor (default: 1)")
    parser.add_argument("--margin", type=float, default=None, help="page margin in mm (default: 0 for auto paper, 5 for named paper)")
    fit_group = parser.add_mutually_exclusive_group()
    fit_group.add_argument("--fit", dest="fit", action="store_true", default=None, help="force fit-to-page")
    fit_group.add_argument("--no-fit", dest="fit", action="store_false", help="keep drawing scale (no fit-to-page)")
    parser.add_argument("-j", "--jobs", type=int, default=0, help="parallel workers across FILES (default: CPU cores; 1 = serial)")
    parser.add_argument("--threads", type=int, default=0, help="parallel workers for chunked rendering WITHIN one big drawing (default: 0 = auto; only active in serial/-j 1 mode)")
    parser.add_argument("--quality", default="balanced", choices=["fast", "balanced", "high"], help="render preset: fast (效率版), balanced (default), high (质量版)")
    parser.add_argument("--core", default="auto", choices=["auto", "go"], help="auto = built-in renderer; go = optional Go fast core (falls back to built-in on any failure)")
    parser.add_argument("--skip-existing", action="store_true", help="skip files whose target PDF already exists")
    parser.add_argument("--font-dir", action="append", default=[], help="extra font directory (SHX/TTF), repeatable")
    parser.add_argument("--report", default=None, help="write a JSON report of the batch run to this file")
    parser.add_argument("--converter-timeout", type=int, default=600, help="seconds allowed per external DWG conversion (default: 600)")
    parser.add_argument("--large-dxf-mb", type=float, default=5.0, help="warn when the intermediate DXF exceeds this size in MB (default: 5)")
    parser.add_argument("--chunk-threshold", type=int, default=20000, help="layouts with at least this many entities use chunked PDF commits (default: 20000; 0 = always)")
    parser.add_argument("--chunk-records", type=int, default=8000, help="recorded commands per chunked page (default: 8000)")
    parser.add_argument("--gui", action="store_true", help="launch the graphical interface")
    parser.add_argument("--check", action="store_true", help="print environment status (converters, libraries) and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose progress output")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print errors and the final summary")
    parser.add_argument("--version", action="version", version=f"dwg2pdf {__version__}")
    return parser


def run_check() -> int:
    """Print what this machine can convert; always exits 0."""
    from .dwg_backend import converter_version, detect_converter
    from .renderer import PYMUPDF_AVAILABLE

    print(f"dwg2pdf {__version__}  |  python {sys.version.split()[0]}  |  {sys.platform}")
    try:
        import ezdxf

        print(f"ezdxf {ezdxf.__version__}: OK")
    except Exception as exc:
        print(f"ezdxf: MISSING ({exc})")
    if PYMUPDF_AVAILABLE:
        import pymupdf

        version = getattr(pymupdf, "__version__", None) or getattr(pymupdf, "VersionBind", "?")
        print(f"pymupdf {version}: OK (PDF backend)")
    else:
        print("pymupdf: MISSING — PDF export unavailable. pip install pymupdf")
    converter = detect_converter()
    if converter:
        print(f"DWG converter: {converter.display_name}")
        print(f"  version: {converter_version(converter)}")
        print("DWG input: supported")
    else:
        from .dwg_backend import INSTALL_HINT

        print("DWG converter: not found")
        print(INSTALL_HINT)
    try:
        from . import fonts as _fonts

        info = _fonts.setup()
        if info["bundled_font"]:
            print(f"bundled CJK font: {info['bundled_font']}")
            print(
                f"  substitutes for {len(info['aliases_registered'])} common "
                f"missing CAD fonts (simsun/hztxt/gbcbig/...): "
                + (", ".join(info["aliases_registered"][:6]) + (" ..." if len(info["aliases_registered"]) > 6 else ""))
            )
        else:
            print("bundled CJK font: MISSING from installation (Chinese text may not render)")
        print(f"fonts visible to renderer: {info['font_count']}")
    except Exception as exc:
        print(f"font setup: FAILED ({exc.__class__.__name__}: {exc})")
    try:
        import tkinter  # noqa: F401

        print("tkinter: OK (--gui available)")
    except Exception:
        print("tkinter: not available on this Python (--gui disabled; CLI unaffected)")
    # GPU: detected and reported honestly. Vector PDF generation is CPU-bound
    # MuPDF work — there is no GPU path in this pipeline, so no --gpu switch
    # is offered (a toggle that changes nothing would be a lie). Multi-core
    # acceleration is the real lever: -j (files) and --threads (chunks).
    gpu = _detect_gpu()
    if gpu:
        print(f"GPU: {gpu}")
        print("  note: vector PDF rendering is CPU-bound; the GPU is not used here.")
        print("  speed up with -j (parallel files) and --threads (parallel chunks).")
    else:
        print("GPU: none detected (not required — rendering is CPU-bound)")
    return 0


def _detect_gpu() -> str | None:
    """Best-effort GPU name for diagnostics; None when not detectable."""
    import shutil as _shutil
    import subprocess as _sp

    smi = _shutil.which("nvidia-smi")
    if smi:
        try:
            proc = _sp.run(
                [smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
            )
            names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if names:
                return ", ".join(names)
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            proc = _sp.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, timeout=15,
            )
            names = [line.strip() for line in proc.stdout.splitlines()[1:] if line.strip()]
            if names:
                return ", ".join(names)
        except Exception:
            pass
    return None


def _print_progress(result, done: int, total: int, quiet: bool, verbose: bool) -> None:
    if quiet:
        return
    name = Path(result.source).name
    if result.status == "ok":
        detail = f"{result.pages} page(s), {result.seconds:.1f}s"
        if verbose:
            print(f"[{done}/{total}] OK    {result.source} -> {result.target} ({detail})", flush=True)
        else:
            print(f"[{done}/{total}] OK    {name} ({detail})", flush=True)
        for warning in result.warnings:
            print(f"[{done}/{total}] WARN  {name}: {warning}", file=sys.stderr, flush=True)
    elif result.status == "skipped":
        print(f"[{done}/{total}] SKIP  {name} (target exists)", flush=True)
    else:
        error = result.error if verbose or len(result.error) <= 300 else result.error[:300] + " ..."
        print(f"[{done}/{total}] FAIL  {name}: {error}", flush=True)


def _heartbeat_loop(state: dict, stop: "threading.Event", interval: float = 30.0, printer=None) -> None:
    """Emit periodic progress signs-of-life until ``stop`` is set.

    Big drawings can keep one file rendering for minutes; without this the
    run looks hung (real user report). Module-level + injectable printer so
    the timing behaviour is unit-testable without waiting 30s.
    """
    emit = printer or (lambda msg: print(msg, flush=True))
    while not stop.wait(interval):
        elapsed = time.monotonic() - state["start"]
        emit(
            f"[heartbeat] {state['done']}/{state['total']} done, "
            f"{elapsed:.0f}s elapsed — still rendering, large drawings "
            "can take minutes per file ..."
        )


def _print_chunk_progress(name: str, done: int, total: int, quiet: bool) -> None:
    """Single-line in-place chunk progress bar for big drawings."""
    if quiet or total <= 0:
        return
    width = 24
    filled = int(width * done / total)
    bar = "#" * filled + "-" * (width - filled)
    pct = 100.0 * done / total
    print(f"\r  [{bar}] {pct:5.1f}%  chunks {done}/{total}  {name[:40]}", end="", flush=True)
    if done >= total:
        print("", flush=True)  # newline when the bar completes


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()  # required for PyInstaller-frozen executables
    _ensure_utf8_stdio()  # must run before argparse (its help text contains CJK)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return run_check()

    if args.gui:
        try:
            from . import gui
        except ImportError as exc:
            print(f"GUI unavailable on this Python: {exc}", file=sys.stderr)
            print("Install a tkinter-enabled Python, or use the CLI.", file=sys.stderr)
            return EXIT_USAGE
        return gui.main()

    if not args.inputs:
        parser.print_help()
        print("\nNothing to do: pass .dwg/.dxf files or directories, or run 'dwg2pdf --gui'.")
        return EXIT_USAGE

    try:
        opts = ConvertOptions(
            layout=args.layout,
            monochrome=args.monochrome,
            paper=args.paper,
            scale=args.scale,
            margin=args.margin,
            fit=args.fit,
            jobs=args.jobs,
            output_dir=args.output_dir,
            skip_existing=args.skip_existing,
            font_dirs=tuple(args.font_dir),
            converter_timeout=args.converter_timeout,
            large_dxf_mb=args.large_dxf_mb,
            chunk_threshold=args.chunk_threshold,
            chunk_records=args.chunk_records,
            threads=args.threads,
            quality=args.quality,
            core=args.core,
            verbose=args.verbose,
        ).validate()
    except OptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if opts.output_dir:
        Path(opts.output_dir).expanduser().mkdir(parents=True, exist_ok=True)

    files, ignored = collect_inputs(args.inputs, recursive=args.recursive)
    for path in ignored:
        p = Path(path)
        if p.is_file():
            print(f"warning: ignoring unsupported file: {path}", file=sys.stderr)
        else:
            print(f"warning: no CAD files found in: {path}", file=sys.stderr)
    if not files:
        print("error: no .dwg/.dxf input files found.", file=sys.stderr)
        return EXIT_USAGE

    if not args.quiet:
        print(f"Converting {len(files)} file(s), jobs={opts.jobs or 'auto'} ...", flush=True)
    plans = plan_targets(files, opts.output_dir)

    # Heartbeat: see _heartbeat_loop. The worker only reads the counter dict;
    # GIL keeps int reads/writes atomic.
    state = {"done": 0, "total": len(plans), "start": time.monotonic()}
    stop_hb = threading.Event()

    hb = None
    if not args.quiet:
        hb = threading.Thread(target=_heartbeat_loop, args=(state, stop_hb), daemon=True)
        hb.start()

    def _progress(r, d, t):
        state["done"] = d
        _print_progress(r, d, t, args.quiet, args.verbose)

    try:
        batch = run_batch(
            plans,
            opts,
            progress_cb=_progress,
            chunk_progress_cb=(
                None if args.quiet
                else lambda name, d, t: _print_chunk_progress(name, d, t, args.quiet)
            ),
        )
    finally:
        stop_hb.set()

    if args.report:
        report_path = Path(args.report).expanduser()
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(batch.to_report(opts), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not args.quiet:
                print(f"report: {report_path.resolve()}")
        except OSError as exc:
            # A failed report must not change the conversion exit code (that
            # would conflate "could not write report" with "a file failed").
            print(f"warning: could not write report to {report_path}: {exc}", file=sys.stderr)

    ok, failed, skipped = len(batch.ok), len(batch.failed), len(batch.skipped)
    print(f"Done in {batch.total_seconds:.1f}s: {ok} ok, {failed} failed, {skipped} skipped.")
    if failed:
        print("Failed files:", file=sys.stderr)
        for r in batch.failed:
            first_line = r.error.splitlines()[0] if r.error else "unknown error"
            print(f"  {r.source}: {first_line}", file=sys.stderr)
        return EXIT_SOME_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
