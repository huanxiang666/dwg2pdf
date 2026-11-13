"""Drawing preview and region (window) export.

Preview renders a layout to a PIL image via a small custom backend that
replays ezdxf Recorder output directly with PIL.ImageDraw — no matplotlib,
no MuPDF commit bottleneck, zero new dependencies (Pillow already required).

Design decisions (measured, not guessed):
- Preview uses its OWN recording with fast curve settings; transforming
  records in place is safe because nothing else shares them.
- Region export records AGAIN at full quality, crops in WCS with
  Player.crop_rect, and replays into the normal PyMuPDF backend — the crop
  shrinks the item count so the single-commit path stays fast.
- Coordinate mapping image px <-> WCS is kept via the placement matrix and
  px-per-pt factor, so a rubber-band rectangle on the canvas maps exactly
  back onto drawing coordinates.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MM_TO_POINTS = 72.0 / 25.4


class _MissingImageBackend(Exception):
    pass


def _require_pil():
    try:
        from PIL import Image, ImageDraw  # noqa: F401

        return Image, ImageDraw
    except ImportError as exc:
        raise _MissingImageBackend(f"Pillow is required for preview: {exc}") from exc


@dataclass
class PreviewImage:
    """Result of render_preview: image + px<->WCS mapping + stats."""

    image: object                     # PIL.Image.Image (RGB)
    px_per_pt: float
    placement_matrix: object          # ezdxf Matrix44 (WCS -> PDF pt)
    width_px: int
    height_px: int
    render_seconds: float = 0.0
    record_seconds: float = 0.0
    record_count: int = 0
    skipped_images: int = 0
    bbox_wcs: tuple = ()              # (xmin, ymin, xmax, ymax) of full layout

    def px_to_wcs(self, x_px: float, y_px: float):
        """Canvas pixel -> drawing (WCS) coordinates.

        Manual 2x2 affine inverse: Matrix44.inverse() returns None for the
        placement matrix (its z column is degenerate), so invert the xy part
        directly — that is all the drawing plane needs.
        """
        m = self.placement_matrix
        # Matrix44 uses tuple indexing m[(row, col)]; row-vector convention:
        # x' = x*m00 + y*m10 + m30 ; y' = x*m01 + y*m11 + m31
        a, c, e = m[(0, 0)], m[(1, 0)], m[(3, 0)]
        b, d, f = m[(0, 1)], m[(1, 1)], m[(3, 1)]
        px = x_px / self.px_per_pt
        py = y_px / self.px_per_pt
        det = a * d - b * c
        if abs(det) < 1e-12:
            raise ValueError("placement matrix is singular")
        dx, dy = px - e, py - f
        return ((d * dx - c * dy) / det, (a * dy - b * dx) / det)


class PillowRenderBackend:
    """Replay target that rasterizes into a PIL image (preview quality).

    Implements the subset of BackendInterface that Player.replay calls:
    configure/set_background/draw_point/draw_line/draw_solid_lines/
    draw_filled_polygon/draw_path/draw_filled_paths/finalize. draw_image is
    counted and skipped (OLE raster frames are rare and not preview-critical).
    """

    def __init__(self, width_px: int, height_px: int, background="#ffffff",
                 supersample: int = 1):
        Image, ImageDraw = _require_pil()
        self._Image, self._ImageDraw = Image, ImageDraw
        self.ss = max(1, int(supersample))
        self.width_px, self.height_px = width_px, height_px
        self._img = Image.new(
            "RGB", (width_px * self.ss, height_px * self.ss), background
        )
        self._draw = ImageDraw.Draw(self._img)
        self._lw_scale = 1.0
        self._px_per_mm = 1.0
        self.skipped_images = 0

    # -- BackendInterface plumbing -------------------------------------
    def configure(self, config) -> None:
        self._lw_scale = float(getattr(config, "lineweight_scaling", 1.0) or 1.0)

    def set_background(self, color) -> None:
        bg = color if isinstance(color, str) else "#ffffff"
        self._img = self._Image.new(
            "RGB", (self.width_px * self.ss, self.height_px * self.ss), bg
        )
        self._draw = self._ImageDraw.Draw(self._img)

    def finalize(self) -> None:
        pass

    # -- helpers ---------------------------------------------------------
    def _color(self, properties) -> tuple:
        try:
            r, g, b = properties.rgb
            return (int(r), int(g), int(b))
        except Exception:
            return (0, 0, 0)

    def _width(self, properties) -> int:
        mm = float(getattr(properties, "lineweight", 0.25) or 0.25)
        w = mm * self._px_per_mm * self._lw_scale * self.ss
        return max(1, int(round(w)))

    def set_px_per_mm(self, px_per_mm: float) -> None:
        self._px_per_mm = float(px_per_mm)

    def _pt(self, v):
        return (v[0] * self.ss, v[1] * self.ss)

    # -- drawing ---------------------------------------------------------
    def draw_point(self, pos, properties) -> None:
        x, y = self._pt(pos)
        c = self._color(properties)
        self._draw.ellipse([x - self.ss, y - self.ss, x + self.ss, y + self.ss], fill=c)

    def draw_line(self, start, end, properties) -> None:
        self._draw.line([self._pt(start), self._pt(end)],
                        fill=self._color(properties), width=self._width(properties))

    def draw_solid_lines(self, lines, properties) -> None:
        c = self._color(properties)
        w = self._width(properties)
        line = self._draw.line
        for a, b in lines:
            line([self._pt(a), self._pt(b)], fill=c, width=w)

    def draw_filled_polygon(self, points, properties) -> None:
        verts = [self._pt(v) for v in points.vertices()]
        if len(verts) < 3:
            if len(verts) == 2:
                self.draw_line(verts[0], verts[1], properties)
            return
        self._draw.polygon(verts, fill=self._color(properties))

    def draw_path(self, path, properties) -> None:
        try:
            verts = [self._pt(v) for v in path.flattening(0.3)]
        except Exception:
            return
        if len(verts) >= 2:
            self._draw.line(verts, fill=self._color(properties),
                            width=self._width(properties), joint="curve")

    def draw_filled_paths(self, paths, properties) -> None:
        c = self._color(properties)
        for path in paths:
            try:
                verts = [self._pt(v) for v in path.flattening(0.3)]
            except Exception:
                continue
            if len(verts) >= 3:
                self._draw.polygon(verts, fill=c)

    def draw_image(self, image_data, properties) -> None:
        self.skipped_images += 1

    # -- result ------------------------------------------------------------
    def get_image(self):
        img = self._img
        if self.ss > 1:
            img = img.resize((self.width_px, self.height_px),
                             self._Image.LANCZOS)
        return img


@dataclass
class PreviewRequest:
    max_px: int = 2400          # long edge of the preview raster
    supersample: int = 0        # 0 = auto (2 for small recordings, 1 for big)
    fast: bool = True           # fast curve settings for the preview record


def _record_layout(doc, layout, fast: bool):
    """Record a layout into a Recorder; returns (rec, seconds)."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
    from ezdxf.addons.drawing.recorder import Recorder

    if fast:
        cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                            circle_approximation_count=36,
                            max_flattening_distance=0.5)
    else:
        cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
    rec = Recorder()
    t0 = time.monotonic()
    Frontend(RenderContext(doc), rec, config=cfg).draw_layout(layout)
    rec.finalize()
    return rec, time.monotonic() - t0


def _layout_of(doc, layout_name: str):
    if layout_name in ("Model", "modelspace", ""):
        return doc.modelspace()
    return doc.layout(layout_name)


# Single-entry DXF document cache: preview and region export hit the same
# (often huge — 57 MB measured) DXF back to back; re-parsing costs ~10 s each
# time. Keyed by (path, mtime, size) so external edits invalidate it. One
# entry only: these documents are memory-hungry.
_doc_cache: dict = {}


def _load_cached_doc(dxf_path: Path):
    from .renderer import _load_dxf_document

    try:
        st = dxf_path.stat()
        key = (str(dxf_path), st.st_mtime, st.st_size)
    except OSError:
        return _load_dxf_document(dxf_path)
    if _doc_cache.get("key") == key:
        return _doc_cache["doc"]
    doc = _load_dxf_document(dxf_path)
    _doc_cache.clear()
    _doc_cache["key"] = key
    _doc_cache["doc"] = doc
    return doc


def render_preview(
    dxf_path: Path | str,
    layout_name: str = "Model",
    req: PreviewRequest | None = None,
) -> PreviewImage:
    """Render one layout to a PreviewImage (PIL raster + px<->WCS mapping)."""
    from ezdxf.addons.drawing import layout as page_layout
    from ezdxf.addons.drawing import pymupdf as _ep  # for get_coordinate_output_space parity

    req = req or PreviewRequest()
    doc = _load_cached_doc(Path(dxf_path))
    layout = _layout_of(doc, layout_name)

    rec, record_seconds = _record_layout(doc, layout, fast=req.fast)
    total = len(rec.records)
    if total == 0:
        raise ValueError("布局没有可渲染内容")

    player = rec.player()
    bbox = player.bbox()
    page_spec = page_layout.Page(0, 0, page_layout.Units.mm,
                                 page_layout.Margins.all(0),
                                 max_width=1189, max_height=841)
    settings = page_layout.Settings(fit_page=True, output_layers=False)
    output_layout = page_layout.Layout(bbox, flip_y=True)
    final_page = output_layout.get_final_page(page_spec, settings)
    inner = __import__("copy").copy(settings)
    inner.output_coordinate_space = _ep.get_coordinate_output_space(final_page)
    matrix = output_layout.get_placement_matrix(final_page, settings=inner,
                                                top_origin=True)

    page_w_pt = final_page.width_in_mm * MM_TO_POINTS
    page_h_pt = final_page.height_in_mm * MM_TO_POINTS
    px_per_pt = req.max_px / max(page_w_pt, page_h_pt)
    w_px = max(64, int(round(page_w_pt * px_per_pt)))
    h_px = max(64, int(round(page_h_pt * px_per_pt)))

    ss = req.supersample
    if ss == 0:
        ss = 2 if total < 60000 else 1

    backend = PillowRenderBackend(w_px, h_px, background="#ffffff", supersample=ss)
    backend.set_px_per_mm(px_per_pt * MM_TO_POINTS)

    player.transform(matrix)                      # WCS -> PDF pt (top-left)
    from ezdxf.math import Matrix44
    scale = Matrix44.scale(px_per_pt * ss)        # pt -> device px (supersampled)
    player.transform(scale)

    t0 = time.monotonic()
    player.replay(backend)
    render_seconds = time.monotonic() - t0

    img = backend.get_image()
    return PreviewImage(
        image=img,
        px_per_pt=px_per_pt,
        placement_matrix=matrix,
        width_px=w_px,
        height_px=h_px,
        render_seconds=render_seconds,
        record_seconds=record_seconds,
        record_count=total,
        skipped_images=backend.skipped_images,
        bbox_wcs=(bbox.extmin.x, bbox.extmin.y, bbox.extmax.x, bbox.extmax.y),
    )


def export_region_pdf(
    dxf_path: Path | str,
    layout_name: str,
    p1_wcs: tuple,
    p2_wcs: tuple,
    out_pdf: Path | str,
    monochrome: bool = False,
    margin_mm: float = 5.0,
) -> dict:
    """Export the WCS rectangle (p1, p2) as a single-page vector PDF.

    Records the layout at FULL quality, crops the recording in WCS, then
    replays the cropped content through the normal PyMuPDF backend. Returns
    a small stats dict (pages, paths_cropped, seconds).
    """
    import copy as _copy

    from ezdxf.addons.drawing import layout as page_layout
    from ezdxf.addons.drawing import pymupdf as _ep
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.math import Vec3

    t0 = time.monotonic()
    doc = _load_cached_doc(Path(dxf_path))
    layout = _layout_of(doc, layout_name)
    rec, _secs = _record_layout(doc, layout, fast=False)
    if not rec.records:
        raise ValueError("布局没有可渲染内容")

    player = rec.player()
    x1, y1 = min(p1_wcs[0], p2_wcs[0]), min(p1_wcs[1], p2_wcs[1])
    x2, y2 = max(p1_wcs[0], p2_wcs[0]), max(p1_wcs[1], p2_wcs[1])
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        raise ValueError("选区无效（面积为零）")
    player.crop_rect(Vec3(x1, y1, 0), Vec3(x2, y2, 0), distance=0.1)

    bbox = player.bbox()
    if not bbox.has_data:
        raise ValueError("所选区域内没有图形内容")

    cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
    if monochrome:
        cfg = cfg.with_changes(color_policy=ColorPolicy.MONOCHROME)

    page_spec = page_layout.Page(0, 0, page_layout.Units.mm,
                                 page_layout.Margins.all(margin_mm),
                                 max_width=1189, max_height=841)
    settings = page_layout.Settings(fit_page=True, output_layers=False)
    output_layout = page_layout.Layout(bbox, flip_y=True)
    final_page = output_layout.get_final_page(page_spec, settings)
    inner = _copy.copy(settings)
    inner.output_coordinate_space = _ep.get_coordinate_output_space(final_page)
    matrix = output_layout.get_placement_matrix(final_page, settings=inner,
                                                top_origin=True)
    player.transform(matrix)

    backend = _ep.PyMuPdfRenderBackend(final_page, inner)
    # configure() normally happens via Frontend; replay calls it for us.
    player.replay(backend)
    pdf_bytes = backend.get_pdf_bytes()

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_pdf.with_name(f"{out_pdf.name}.part{os.getpid()}")
    tmp.write_bytes(pdf_bytes)
    os.replace(tmp, out_pdf)
    return {
        "pages": 1,
        "records_after_crop": len(player.records),
        "seconds": round(time.monotonic() - t0, 2),
        "path": str(out_pdf),
    }


# ------------------------------------------------------------- open / print
def open_path(path: Path | str) -> None:
    """Open a file with the platform default viewer."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def print_pdf(path: Path | str) -> str:
    """Send a PDF to the default printer; returns a human-readable status."""
    path = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(path, "print")  # type: ignore[attr-defined]
            return "已发送到系统默认打印机"
        if sys.platform == "darwin":
            subprocess.Popen(["lpr", path])
            return "已发送到系统默认打印机（lpr）"
        if os.path.exists(path):
            lp = subprocess.run(["lp", path], capture_output=True, text=True, timeout=30)
            if lp.returncode == 0:
                return f"已发送到打印机：{lp.stdout.strip() or 'lp'}"
            # No CUPS queue etc.: fall back to opening the viewer (Ctrl+P).
            open_path(path)
            return "未检测到可用打印队列，已打开 PDF——请用查看器的打印按钮"
    except FileNotFoundError:
        open_path(path)
        return "未找到打印命令，已打开 PDF——请用查看器的打印按钮"
    except Exception as exc:
        open_path(path)
        return f"打印调用失败（{exc.__class__.__name__}），已改为打开 PDF"
    return "已打开 PDF"
