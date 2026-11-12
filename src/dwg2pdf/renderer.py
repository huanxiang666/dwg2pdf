"""DXF -> PDF rendering on top of ezdxf's drawing add-on (PyMuPDF backend).

The renderer picks the layouts to export, builds page geometry (auto-detected
for modelspace, the drawing's own paper size for paperspace layouts) and merges
all exported layouts into a single multi-page PDF per input file.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as page_layout
from ezdxf.addons.drawing.config import (
    BackgroundPolicy,
    ColorPolicy,
    Configuration,
)

from .options import ConvertOptions

try:
    from ezdxf.addons.drawing.pymupdf import MM_TO_POINTS, PyMuPdfBackend
    import pymupdf

    PYMUPDF_AVAILABLE = True
    _PYMUPDF_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    PyMuPdfBackend = None  # type: ignore[assignment]
    pymupdf = None  # type: ignore[assignment]
    PYMUPDF_AVAILABLE = False
    _PYMUPDF_IMPORT_ERROR = exc


class RenderError(RuntimeError):
    """A DXF file could not be rendered to PDF."""


def _install_lenient_sortentstable() -> None:
    """Tolerate corrupted SORTENTSTABLE objects written by LibreDWG.

    Observed on a real r2007 architectural DWG (5.3 MB, 41k entities):
    dwg2dxf emitted a 36733-entry SORTENTSTABLE where one ``331 <handle>``
    entry lost its paired ``5 <sort-handle>``, shifting every following pair.
    ezdxf raises a fatal DXFStructureError on that — and recover.readfile()
    propagates it too, because it happens during entity binding, not tag
    recovery. SORTENTSTABLE only stores AutoCAD's draw-order bookkeeping and
    is irrelevant for rendering, so we load it leniently: keep well-formed
    (331, 5) pairs, skip anything else. Idempotent; a no-op if ezdxf's
    internals change shape.
    """
    try:
        from ezdxf.entities import dxfobj

        cls = dxfobj.SortEntsTable
        if getattr(cls.load_table, "_dwg2pdf_lenient", False):
            return

        def load_table(self, tags):  # noqa: ANN001 - mirrors ezdxf signature
            table: dict[str, str] = {}
            i, n = 0, len(tags)
            while i < n:
                handle = tags[i]
                if handle.code != 331:
                    i += 1
                    continue
                if i + 1 < n and tags[i + 1].code == 5:
                    table[handle.value] = tags[i + 1].value
                    i += 2
                else:
                    # Corrupted pair: drop this 331 and resync at the next tag.
                    i += 1
            self.table = table

        load_table._dwg2pdf_lenient = True  # type: ignore[attr-defined]
        cls.load_table = load_table
    except Exception:
        # Never let the hardening break the import; strict behaviour remains.
        pass


_install_lenient_sortentstable()


@dataclass
class RenderResult:
    """Outcome of rendering one DXF file."""

    pages: int = 0
    layouts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def require_pymupdf() -> None:
    """Raise a helpful error when the PyMuPDF backend is unavailable."""
    if not PYMUPDF_AVAILABLE:
        raise RenderError(
            "PyMuPDF is required for PDF export but could not be imported "
            f"({_PYMUPDF_IMPORT_ERROR!r}). Install it with: pip install pymupdf"
        )


def setup_font_dirs(font_dirs: tuple[str, ...] | list[str]) -> None:
    """Initialize the font subsystem for this process.

    Rebuilds ezdxf's font manager over system dirs + the bundled CJK font +
    user font dirs, and registers substitute aliases for common missing
    Chinese CAD fonts (see dwg2pdf.fonts). Idempotent per-process.
    """
    from . import fonts as _fonts

    # Also keep ezdxf's own support_dirs updated for anything that consults
    # the raw option (harmless duplication, cheap).
    for d in font_dirs or ():
        d = str(d)
        if d and d not in ezdxf.options.support_dirs:
            ezdxf.options.support_dirs.append(d)
    _fonts.setup(tuple(str(d) for d in (font_dirs or ())))


def list_layout_names(doc: ezdxf.document.Drawing) -> list[str]:
    """All layout names of a document in tab order, including 'Model'."""
    return [layout.name for layout in doc.layouts]


def select_layouts(doc: ezdxf.document.Drawing, mode: str) -> list[tuple[str, object]]:
    """Resolve the layout selection mode to concrete (name, layout) pairs.

    Modes: "auto", "modelspace", "all", or an exact layout name.
    "auto" prefers paperspace layouts that actually contain entities and falls
    back to modelspace — this matches the usual batch-print expectation
    (title-block layouts win over raw modelspace, blank default layouts are
    ignored).
    """
    modelspace = doc.modelspace()
    if mode == "modelspace":
        return [("Model", modelspace)]

    paperspace = [layout for layout in doc.layouts if layout.name != "Model"]
    if mode == "all":
        return [("Model", modelspace)] + [(l.name, l) for l in paperspace]

    if mode == "auto":
        used = [(l.name, l) for l in paperspace if len(l) > 0]
        if used:
            return used
        return [("Model", modelspace)]

    # Exact layout name.
    if mode.lower() == "model":
        return [("Model", modelspace)]
    for layout in paperspace:
        if layout.name == mode:
            return [(layout.name, layout)]
    available = ", ".join(list_layout_names(doc))
    raise RenderError(f"layout {mode!r} not found in drawing (available: {available})")


def _build_config(opts: ConvertOptions) -> Configuration:
    """Render configuration: white background, optional mono, quality preset."""
    from .options import QUALITY_PRESETS

    preset = QUALITY_PRESETS.get(opts.quality, {})
    config = Configuration(background_policy=BackgroundPolicy.WHITE, **preset)
    if opts.monochrome:
        config = config.with_changes(color_policy=ColorPolicy.MONOCHROME)
    return config


def _load_dxf_document(path: Path):
    """Load a DXF document, falling back to ezdxf's tolerant recover loader.

    Real-world DXF files (and files produced by external converters like
    dwg2dxf) can carry structural defects — duplicate or invalid handles,
    broken tables — that make the strict loader raise. ``ezdxf.recover``
    repairs what it can instead of giving up.
    """
    try:
        return ezdxf.readfile(str(path))
    except IOError as exc:
        raise RenderError(f"cannot read file: {exc}") from exc
    except Exception as strict_exc:
        from ezdxf import recover

        try:
            doc, _auditor = recover.readfile(str(path))
            return doc
        except IOError as exc:
            raise RenderError(f"cannot read file: {exc}") from exc
        except Exception as recover_exc:
            raise RenderError(
                f"corrupt or unsupported DXF structure: {recover_exc}; "
                f"strict loader said: {strict_exc}"
            ) from recover_exc


def _content_is_portrait(layout) -> bool | None:
    """True/False from the layout's bounding box aspect; None when unknown."""
    try:
        from ezdxf import bbox

        extents = bbox.extents(layout, fast=True)
        if extents is None or not extents.has_data:
            return None
        size = extents.size
        if size.x <= 0 and size.y <= 0:
            return None
        return bool(size.y > size.x)
    except Exception:
        return None


def _named_page(name: str, opts: ConvertOptions, layout) -> page_layout.Page:
    """Build a fixed-size Page, choosing orientation to match content."""
    width, height, units = page_layout.PAGE_SIZES[name]
    portrait = _content_is_portrait(layout)
    if portrait is True and width > height:
        width, height = height, width
    elif portrait is False and height > width:
        width, height = height, width
    margin = opts.margin if opts.margin is not None else 5.0
    return page_layout.Page(width, height, units, page_layout.Margins.all(margin))


def _fallback_page(opts: ConvertOptions) -> page_layout.Page:
    """Plain ISO A4 landscape page for empty/degenerate content.

    Auto page detection divides by the content bounding box; an empty drawing
    would produce NaN geometry, so we fall back to a printable default.
    """
    margin = opts.margin if opts.margin is not None else 10.0
    return page_layout.Page(297, 210, page_layout.Units.mm, page_layout.Margins.all(margin))


def _has_renderable_content(layout) -> bool:
    try:
        from ezdxf import bbox

        extents = bbox.extents(layout, fast=True)
        if extents is None or not extents.has_data:
            return False
        size = extents.size
        return bool(size.x > 1e-9 or size.y > 1e-9)
    except Exception:
        # When in doubt, assume there is content and let the renderer decide.
        return True


def _page_and_settings(layout, is_modelspace: bool, opts: ConvertOptions):
    """Resolve (Page, Settings) for one layout following CAD plot semantics."""
    fit = opts.fit
    if opts.paper != "auto":
        page = _named_page(opts.paper, opts, layout)
        settings = page_layout.Settings(
            scale=opts.scale,
            fit_page=True if fit is None else fit,
        )
        return page, settings

    if is_modelspace:
        if not _has_renderable_content(layout):
            return _fallback_page(opts), page_layout.Settings(
                scale=opts.scale, fit_page=True
            )
        margin = opts.margin if opts.margin is not None else 0.0
        # Auto-detect page size from content, capped at ISO A0 (largest
        # printable engineering sheet).
        page = page_layout.Page(
            0,
            0,
            page_layout.Units.mm,
            page_layout.Margins.all(margin),
            max_width=1189,
            max_height=841,
        )
        settings = page_layout.Settings(
            scale=opts.scale,
            fit_page=True if fit is None else fit,
        )
        return page, settings

    # Paperspace with auto paper: honor the layout's own page size and the
    # plot scale configured inside the CAD file (same as printing the layout).
    try:
        page = page_layout.Page.from_dxf_layout(layout)
    except Exception:
        page = None
    if page is None or page.width <= 0 or page.height <= 0:
        # Layout carries no usable paper settings — printable default instead.
        return _fallback_page(opts), page_layout.Settings(
            scale=opts.scale, fit_page=True
        )
    if opts.margin is not None:
        page = page_layout.Page(
            page.width,
            page.height,
            page.units,
            page_layout.Margins.all(opts.margin),
        )
    try:
        cad_scale = layout.get_plot_unit_scale_factor()
    except Exception:
        cad_scale = 1.0
    settings = page_layout.Settings(
        scale=cad_scale * opts.scale,
        fit_page=False if fit is None else fit,
    )
    return page, settings


def _page_to_points(page: page_layout.Page) -> tuple[float, float]:
    """Convert a Page's (width, height) into PDF points."""
    units = page.units
    if units == page_layout.Units.mm:
        factor = MM_TO_POINTS
    elif units == page_layout.Units.cm:
        factor = MM_TO_POINTS * 10
    elif units == page_layout.Units.inch:
        factor = 72.0
    elif units == page_layout.Units.pt:
        factor = 1.0
    else:  # px (96 dpi CSS pixel)
        factor = 0.75
    return page.width * factor, page.height * factor


def _blank_page_for(opts: ConvertOptions) -> page_layout.Page:
    """Page geometry used for empty layouts (named paper wins, else A4)."""
    if opts.paper != "auto":
        width, height, units = page_layout.PAGE_SIZES[opts.paper]
        margin = opts.margin if opts.margin is not None else 5.0
        return page_layout.Page(width, height, units, page_layout.Margins.all(margin))
    return _fallback_page(opts)


def _entity_count(layout) -> int:
    """Number of entities in a layout (cheap; drives the chunked path)."""
    try:
        return len(layout)
    except Exception:
        return 0


def _replay_chunk(job: tuple) -> bytes:
    """Render one recorded chunk to PDF bytes. Module-level so it is picklable.

    job = (records, config, background, properties, matrix, final_page,
    settings). All elements verified picklable, so this runs in a process-pool
    worker: rebuild a Player over the record slice, apply the shared placement
    matrix, replay into a fresh PyMuPDF backend.
    """
    from ezdxf.addons.drawing import pymupdf as _ep
    from ezdxf.addons.drawing.recorder import Player

    records, config, background, properties, matrix, final_page, settings = job
    player = Player()
    player.config = config
    player.background = background
    player.records = list(records)
    player.properties = properties
    player.has_shared_recordings = False
    player.transform(matrix)
    render_backend = _ep.PyMuPdfRenderBackend(final_page, settings)
    player.replay(render_backend)
    return render_backend.get_pdf_bytes()


def _render_layout_chunked(
    doc,
    layout,
    page,
    settings,
    config,
    chunk_size: int,
    workers: int = 1,
    progress=None,
) -> list[bytes]:
    """Render one layout as several same-framed pages, committing per chunk.

    Why: PyMuPdfRenderBackend keeps ONE shape for the whole drawing and commits
    it in ``finalize()``. For big drawings that single commit is superlinear —
    measured on an 85 364-record / 1.54M-item architectural drawing: >15 min
    unfinished, while the same geometry in 11 chunks of 8 000 records finished
    in 123 s. Geometry is identical; only the commit size changes.

    Every chunk shares one placement matrix computed from the FULL bounding
    box, so all produced pages have the same frame and coordinate system.

    ``workers > 1`` renders chunks in a process pool — the real speed lever for
    big drawings (vector PDF generation is CPU-bound MuPDF work; there is no
    GPU path in this pipeline). Chunk order is preserved. ``progress(done,
    total)`` is called in the parent process as chunks complete.
    """
    from ezdxf.addons.drawing import pymupdf as _ep
    from ezdxf.addons.drawing.recorder import Recorder

    rec = Recorder()
    Frontend(RenderContext(doc), rec, config=config).draw_layout(layout)
    rec.finalize()
    records = rec.records
    total = len(records)
    if total == 0:
        return []

    full = rec.player()
    output_layout = page_layout.Layout(full.bbox(), flip_y=True)
    final_page = output_layout.get_final_page(page, settings)
    inner = copy.copy(settings)
    inner.output_coordinate_space = _ep.get_coordinate_output_space(final_page)
    matrix = output_layout.get_placement_matrix(final_page, settings=inner, top_origin=True)

    slices = [records[start : start + chunk_size] for start in range(0, total, chunk_size)]
    # Player state is rebuilt per worker from the recorder's own fields
    # (config/background/properties) — mirrors Recorder.player().
    rec_config = getattr(rec, "config", config)
    rec_background = getattr(rec, "background", "#000000")
    rec_properties = dict(getattr(rec, "properties", {}) or {})
    jobs = [
        (sl, rec_config, rec_background, rec_properties, matrix, final_page, inner)
        for sl in slices
    ]
    n_chunks = len(jobs)

    # Never nest process pools: batch workers already parallelise across files.
    import multiprocessing as _mp

    if _mp.parent_process() is not None:
        workers = 1

    def _report(done: int) -> None:
        if progress is not None:
            try:
                progress(done, n_chunks)
            except Exception:
                pass  # progress display must never kill a render

    if workers <= 1 or n_chunks == 1:
        pages = []
        for job in jobs:
            pages.append(_replay_chunk(job))
            _report(len(pages))
        return pages

    try:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        pages: list[bytes | None] = [None] * n_chunks
        with ProcessPoolExecutor(max_workers=min(workers, n_chunks)) as pool:
            futures = {pool.submit(_replay_chunk, job): i for i, job in enumerate(jobs)}
            done = 0
            for future in as_completed(futures):
                pages[futures[future]] = future.result()
                done += 1
                _report(done)
        if all(p is not None for p in pages):
            return pages  # type: ignore[return-value]
    except Exception:
        # Any pool-level failure: rerun serially rather than losing the file.
        pass
    pages = []
    for job in jobs:
        pages.append(_replay_chunk(job))
        _report(len(pages))
    return pages


def render_dxf_to_pdf(
    dxf_path: Path | str,
    pdf_path: Path | str,
    opts: ConvertOptions | None = None,
    progress=None,
) -> RenderResult:
    """Render one DXF file to a (multi-page) PDF. Raises RenderError.

    ``progress(done_chunks, total_chunks)`` (optional) reports chunked-render
    progress for big layouts — called from this process only.
    """
    require_pymupdf()
    opts = (opts or ConvertOptions()).validate()
    dxf_path = Path(dxf_path)
    pdf_path = Path(pdf_path)
    setup_font_dirs(opts.font_dirs)
    doc = _load_dxf_document(dxf_path)

    try:
        layouts = select_layouts(doc, opts.layout)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"layout selection failed: {exc.__class__.__name__}: {exc}") from exc

    config = _build_config(opts)
    merged = pymupdf.open()
    result = RenderResult()
    try:
        for name, layout in layouts:
            if not _has_renderable_content(layout):
                # Empty layout: the render pipeline divides by the content
                # bounding box and would hit NaN/ValueError — emit a clean
                # blank page of the target paper size instead.
                width_pt, height_pt = _page_to_points(_blank_page_for(opts))
                merged.new_page(width=width_pt, height=height_pt)
                result.pages += 1
                result.layouts.append(name)
                continue
            backend = PyMuPdfBackend()
            frontend = Frontend(RenderContext(doc), backend, config=config)
            page, settings = _page_and_settings(layout, name == "Model", opts)

            # Chunked path for big layouts: PyMuPdfRenderBackend commits the
            # whole drawing through a single shape, which becomes superlinear
            # (measured >15 min unfinished on 85k records vs 123 s chunked).
            entity_count = _entity_count(layout)
            if entity_count >= opts.chunk_threshold:
                workers = opts.threads if opts.threads > 0 else (os.cpu_count() or 1)
                try:
                    page_bytes_list = _render_layout_chunked(
                        doc, layout, page, settings, config, opts.chunk_records,
                        workers=workers, progress=progress,
                    )
                except Exception as exc:
                    raise RenderError(
                        f"chunked rendering of layout {name!r} failed: "
                        f"{exc.__class__.__name__}: {exc}"
                    ) from exc
                for pdf_bytes in page_bytes_list:
                    part = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                    try:
                        merged.insert_pdf(part)
                    finally:
                        part.close()
                result.pages += len(page_bytes_list)
                result.layouts.append(name)
                result.warnings.append(
                    f"layout {name!r}: {entity_count} entities rendered in "
                    f"{len(page_bytes_list)} same-frame page(s) to keep PDF "
                    "generation fast (chunked commit)"
                )
                continue

            try:
                frontend.draw_layout(layout)
            except Exception as exc:
                raise RenderError(
                    f"rendering layout {name!r} failed: {exc.__class__.__name__}: {exc}"
                ) from exc
            try:
                pdf_bytes = backend.get_pdf_bytes(page, settings=settings)
            except ValueError as exc:
                # Degenerate content extents (entities that exist but carry no
                # renderable geometry, or NaN coordinates in broken files):
                # emit a blank page + a loud warning instead of failing the
                # whole file. Other ValueErrors stay fatal.
                # LIMIT: signature-based fallback; replace with sanitized
                # re-render if users need the actual geometry rescued.
                msg = str(exc)
                if "empty bounding box" in msg or "NaN" in msg:
                    result.warnings.append(
                        f"layout {name!r}: degenerate geometry produced no renderable "
                        f"content; exported a blank page instead ({msg})"
                    )
                    width_pt, height_pt = _page_to_points(_blank_page_for(opts))
                    merged.new_page(width=width_pt, height=height_pt)
                    result.pages += 1
                    result.layouts.append(name)
                    continue
                raise RenderError(
                    f"PDF export of layout {name!r} failed: ValueError: {exc}"
                ) from exc
            except Exception as exc:
                raise RenderError(
                    f"PDF export of layout {name!r} failed: {exc.__class__.__name__}: {exc}"
                ) from exc
            part = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                merged.insert_pdf(part)
            finally:
                part.close()
            result.pages += 1
            result.layouts.append(name)

        if merged.page_count == 0:
            raise RenderError("no pages produced (empty drawing?)")

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = pdf_path.with_name(f"{pdf_path.name}.part{os.getpid()}")
        try:
            merged.save(str(tmp_path), garbage=3, deflate=True)
            merged.close()
            os.replace(tmp_path, pdf_path)
        except Exception:
            # Don't leak the .partNNNN scratch file if save or the atomic
            # replace fails (e.g. disk full, permission, path vanished).
            if not merged.is_closed:
                merged.close()
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise
    except Exception:
        if not merged.is_closed:
            merged.close()
        raise
    return result
