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
