"""Renderer tests: layout selection, page geometry, monochrome, error paths."""

from __future__ import annotations

import ezdxf
import pymupdf
import pytest
from pypdf import PdfReader

from conftest import write_colored_dxf, write_corrupt_dxf

from dwg2pdf.options import ConvertOptions
from dwg2pdf.renderer import RenderError, render_dxf_to_pdf, select_layouts


def _page_sizes(pdf_path):
    doc = pymupdf.open(str(pdf_path))
    try:
        return [(p.rect.width, p.rect.height) for p in doc]
    finally:
        doc.close()


def test_render_modelspace_produces_valid_pdf(tmp_path, sample_dxf):
    out = tmp_path / "model.pdf"
    result = render_dxf_to_pdf(sample_dxf, out, ConvertOptions(layout="modelspace"))
    assert out.exists()
    assert result.pages == 1
    assert result.layouts == ["Model"]
    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert float(box.width) > 0 and float(box.height) > 0


def test_render_auto_prefers_nonempty_paperspace(tmp_path, sample_dxf):
    out = tmp_path / "auto.pdf"
    result = render_dxf_to_pdf(sample_dxf, out, ConvertOptions(layout="auto"))
    # sample.dxf has a non-empty Layout1 → auto must pick it, not Model
    assert result.layouts == ["Layout1"]
    assert result.pages == 1


def test_render_auto_falls_back_to_modelspace_when_paperspace_empty(tmp_path):
    dxf = tmp_path / "empty_layout.dxf"
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (50, 0))
    # Layout1 exists (ezdxf default) but stays empty
    doc.saveas(str(dxf))
    result = render_dxf_to_pdf(dxf, tmp_path / "out.pdf", ConvertOptions(layout="auto"))
    assert result.layouts == ["Model"]


def test_render_all_layouts_multipage(tmp_path, sample_dxf):
    out = tmp_path / "all.pdf"
    result = render_dxf_to_pdf(sample_dxf, out, ConvertOptions(layout="all"))
    assert result.layouts == ["Model", "Layout1"]
    assert result.pages == 2
    assert len(PdfReader(str(out)).pages) == 2


def test_render_named_layout_and_unknown_layout(tmp_path, sample_dxf):
    out = tmp_path / "layout1.pdf"
    result = render_dxf_to_pdf(sample_dxf, out, ConvertOptions(layout="Layout1"))
    assert result.layouts == ["Layout1"]
    with pytest.raises(RenderError, match="not found"):
        render_dxf_to_pdf(sample_dxf, tmp_path / "nope.pdf", ConvertOptions(layout="NoSuchLayout"))


def test_monochrome_removes_color(tmp_path):
    """Mutation-proof pair: same source renders red in color mode and no red
    in monochrome mode."""
    src = write_colored_dxf(tmp_path / "colors.dxf")

    color_pdf = tmp_path / "color.pdf"
    render_dxf_to_pdf(src, color_pdf, ConvertOptions(layout="modelspace"))
    mono_pdf = tmp_path / "mono.pdf"
    render_dxf_to_pdf(src, mono_pdf, ConvertOptions(layout="modelspace", monochrome=True))

    def has_saturated_stroke(path):
        doc = pymupdf.open(str(path))
        try:
            for page in doc:
                for drawing in page.get_drawings():
                    color = drawing.get("color")  # stroke color (r, g, b) 0..1
                    if color and max(color) - min(color) > 0.2:
                        return True
            return False
        finally:
            doc.close()

    assert has_saturated_stroke(color_pdf), "color control: red/blue lines must survive color render"
    assert not has_saturated_stroke(mono_pdf), "monochrome render still contains colored strokes"


def test_named_paper_a4_page_size(tmp_path, sample_dxf):
    out = tmp_path / "a4.pdf"
    render_dxf_to_pdf(sample_dxf, out, ConvertOptions(layout="modelspace", paper="ISO A4"))
    (w, h) = _page_sizes(out)[0]
    # A4 = 297 x 210 mm = 841.89 x 595.28 pt (orientation may swap)
    assert sorted((w, h)) == pytest.approx(sorted((841.89, 595.28)), abs=2.0)


def test_paperspace_keeps_own_page_size(tmp_path, sample_dxf):
    out = tmp_path / "ps.pdf"
    render_dxf_to_pdf(sample_dxf, out, ConvertOptions(layout="Layout1", paper="auto"))
    (w, h) = _page_sizes(out)[0]
    # Layout1 of an ezdxf.new document defaults to a real paper size (not 0)
    assert w > 50 and h > 50


def test_corrupt_dxf_raises_render_error(tmp_path):
    src = write_corrupt_dxf(tmp_path / "corrupt.dxf")
    with pytest.raises(RenderError):
        render_dxf_to_pdf(src, tmp_path / "corrupt.pdf", ConvertOptions())


def test_empty_drawing_renders_without_crash(tmp_path):
    """Boundary case: a drawing with zero entities must not crash the batch."""
    src = tmp_path / "empty.dxf"
    ezdxf.new("R2010").saveas(str(src))
    out = tmp_path / "empty.pdf"
    result = render_dxf_to_pdf(src, out, ConvertOptions(layout="modelspace"))
    assert result.pages == 1
    assert out.exists()


def test_select_layouts_lists_available_names_in_error(tmp_path, sample_dxf):
    doc = ezdxf.readfile(str(sample_dxf))
    with pytest.raises(RenderError) as excinfo:
        select_layouts(doc, "Missing")
    assert "Model" in str(excinfo.value)


# ------------------------------------------------- corrupt SORTENTSTABLE
# Regression from a real file: LibreDWG dwg2dxf output of a 5.3 MB r2007
# architectural DWG ("(3审)建施_9#楼_J00~J18_t3(1).dwg") contained a
# SORTENTSTABLE whose entry #769 lost its sort-handle (331 followed by 331),
# misaligning all 36k following pairs. ezdxf raised a fatal DXFStructureError
# on it — recover.readfile() too, since the failure happens during entity
# binding. SORTENTSTABLE is pure draw-order bookkeeping; rendering must not
# die on it.

_CORRUPT_SORTENTS_LINES = [
    "  0", "SORTENTSTABLE",
    "  5", "FFFFF001",
    "100", "AcDbSortentsTable",
    "331", "AAAA",
    "331", "BBBB",   # corrupt: missing the "5" sort-handle after AAAA
    "  5", "CCCC",
]


def _inject_corrupt_sortentstable(dxf_path) -> None:
    raw = dxf_path.read_bytes().decode("utf-8", errors="surrogateescape")
    lines = raw.splitlines()
    # locate OBJECTS section, then its ENDSEC; insert before the "  0" that
    # precedes ENDSEC
    obj_start = None
    for i in range(len(lines) - 1):
        if lines[i].strip() == "2" and lines[i + 1].strip() == "OBJECTS":
            obj_start = i
            break
    assert obj_start is not None, "generated DXF has no OBJECTS section"
    end_idx = next(j for j in range(obj_start, len(lines)) if lines[j].strip() == "ENDSEC")
    assert lines[end_idx - 1].strip() == "0"
    new_lines = lines[: end_idx - 1] + _CORRUPT_SORTENTS_LINES + lines[end_idx - 1:]
    dxf_path.write_bytes(
        ("\r\n".join(new_lines) + "\r\n").encode("utf-8", errors="surrogateescape")
    )


def test_lenient_sortentstable_skips_corrupt_pairs():
    from ezdxf.entities.dxfobj import SortEntsTable
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag

    obj = SortEntsTable()
    tags = Tags([
        DXFTag(331, "A"), DXFTag(5, "SA"),   # well-formed
        DXFTag(331, "B"),                    # corrupt: sort handle missing
        DXFTag(331, "C"), DXFTag(5, "SC"),   # must resync here
    ])
    obj.load_table(tags)  # must not raise
    assert obj.table == {"A": "SA", "C": "SC"}


def test_corrupt_sortentstable_dxf_renders(tmp_path):
    from ezdxf.entities import dxfobj

    from dwg2pdf.renderer import render_dxf_to_pdf

    src = tmp_path / "corrupt-sortents.dxf"
    doc = ezdxf.new("R2010", setup=True)
    doc.modelspace().add_line((0, 0), (100, 50), dxfattribs={"color": 1})
    doc.saveas(str(src))
    _inject_corrupt_sortentstable(src)

    patched = dxfobj.SortEntsTable.load_table
    assert getattr(patched, "_dwg2pdf_lenient", False), "lenient loader not installed"

    # Red control: ezdxf's original strict logic must reject this exact file —
    # proves the fixture really carries the corruption (mutation-safe test).
    def strict_load_table(self, tags):
        for handle, sort_handle in dxfobj.take2(tags):
            if handle.code != 331:
                raise ezdxf.DXFStructureError(
                    f"Invalid handle code {handle.code}, expected 331")
            if sort_handle.code != 5:
                raise ezdxf.DXFStructureError(
                    f"Invalid sort handle code {handle.code}, expected 5")
            self.table[handle.value] = sort_handle.value

    try:
        dxfobj.SortEntsTable.load_table = strict_load_table
        with pytest.raises(ezdxf.DXFStructureError, match="sort handle"):
            ezdxf.readfile(str(src))
    finally:
        dxfobj.SortEntsTable.load_table = patched

    out = tmp_path / "out.pdf"
    result = render_dxf_to_pdf(src, out, ConvertOptions(layout="modelspace"))
    assert result.pages == 1
    assert out.read_bytes()[:5] == b"%PDF-"


# ------------------------------------------------------- chunked commit path
# PyMuPdfRenderBackend commits the whole drawing through ONE shape, which is
# superlinear in item count: an 85 364-record architectural drawing ran >15 min
# without finishing, while the same geometry committed in 8 000-record chunks
# finished in 123 s. Large layouts therefore render as several same-framed
# pages. These tests pin the behaviour (frame identity + full geometry).

def test_chunked_render_produces_pages_with_identical_frame(tmp_path):
    from dwg2pdf.renderer import render_dxf_to_pdf

    # A drawing comfortably above a forced-low threshold
    src = tmp_path / "many.dxf"
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    for i in range(60):
        msp.add_line((i, 0), (i, 100), dxfattribs={"color": 2})
    doc.saveas(str(src))

    plain = tmp_path / "plain.pdf"
    r_plain = render_dxf_to_pdf(src, plain, ConvertOptions(layout="modelspace"))
    assert r_plain.pages == 1

    chunked = tmp_path / "chunked.pdf"
    r_chunked = render_dxf_to_pdf(
        src, chunked,
        ConvertOptions(layout="modelspace", chunk_threshold=1, chunk_records=10),
    )
    assert r_chunked.pages > 1, "low threshold must engage the chunked path"
    assert any("chunked commit" in w for w in r_chunked.warnings)

    import pymupdf as _pm

    d = _pm.open(str(chunked))
    try:
        frames = {(round(p.rect.width, 1), round(p.rect.height, 1)) for p in d}
        assert len(frames) == 1, f"all chunk pages must share one frame, got {frames}"
        paths = sum(len(p.get_drawings()) for p in d)
    finally:
        d.close()
    # geometry preserved: every line appears exactly once across the pages
    assert paths >= 60, f"lost geometry: only {paths} paths for 60 lines"


def test_chunked_render_geometry_matches_single_page(tmp_path):
    """Union of the chunked pages must cover the same ink as the single page."""
    import pymupdf as _pm

    from dwg2pdf.renderer import render_dxf_to_pdf

    src = tmp_path / "d.dxf"
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    for i in range(40):
        msp.add_circle((i * 10, 0), 4, dxfattribs={"color": 1})
    doc.saveas(str(src))

    one = tmp_path / "one.pdf"
    render_dxf_to_pdf(src, one, ConvertOptions(layout="modelspace"))
    many = tmp_path / "many.pdf"
    render_dxf_to_pdf(src, many, ConvertOptions(
        layout="modelspace", chunk_threshold=1, chunk_records=8))

    def masks(path):
        d = _pm.open(str(path))
        try:
            out = []
            for p in d:
                pix = p.get_pixmap(dpi=30)
                import numpy as np
                from PIL import Image
                a = np.asarray(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)).astype(int)
                out.append(a.min(axis=2) < 245)
            return out
        finally:
            d.close()

    single = masks(one)[0]
    union = masks(many)
    if len(union) == 1:
        u = union[0]
    else:
        u = union[0] | union[1]
        for m in union[2:]:
            u = u | m
    assert single.shape == u.shape
    iou = (single & u).sum() / max((single | u).sum(), 1)
    assert iou > 0.6, f"chunked union diverges from single page (IoU={iou:.2f})"
