"""Font subsystem tests: bundled CJK font, alias substitution, user dirs.

Hermeticity: tests monkeypatch ezdxf's platform font directories to an empty
folder so the simulated machine has NO system fonts — otherwise this dev/CI
machine's own Noto/Droid fonts would mask a broken substitution path.
"""

from __future__ import annotations

import pytest

import ezdxf
from ezdxf.enums import TextEntityAlignment

from dwg2pdf import fonts as f


@pytest.fixture
def clean_machine(monkeypatch, tmp_path):
    """Simulate a machine with zero system fonts; restore state afterwards."""
    from ezdxf.fonts import font_manager as ez_fm
    from ezdxf.fonts import fonts as ez_fonts

    empty = tmp_path / "no-fonts"
    empty.mkdir()
    monkeypatch.setitem(ez_fm.FONT_DIRECTORIES, ez_fonts.font_manager.platform, [str(empty)])
    f._setup_cache.clear()
    yield
    f._setup_cache.clear()
    # Rebuild against the real system so later tests see normal fonts.
    ez_fonts.font_manager.build()


class TestSetup:
    def test_bundled_font_discovered(self):
        d = f.bundled_font_dir()
        assert d is not None, "bundled font_data dir must ship with the package"
        assert (d / f.BUNDLED_FONT_NAME).is_file()

    def test_aliases_registered_on_clean_machine(self, clean_machine):
        from ezdxf.fonts import fonts as ez_fonts

        info = f.setup()
        assert info["bundled_font"] and info["font_count"] >= 1
        cache = ez_fonts.font_manager._font_cache
        for alias in ("simsun.ttf", "hztxt.shx", "gbcbig.shx"):
            assert alias in cache, f"{alias} must resolve via substitution"
            assert cache[alias].file_path.name == f.BUNDLED_FONT_NAME

    def test_real_font_not_shadowed(self, clean_machine, monkeypatch, tmp_path):
        """A user-supplied real simsun.ttf must win over the bundled alias."""
        from ezdxf.fonts import fonts as ez_fonts

        user_dir = tmp_path / "userfonts"
        user_dir.mkdir()
        # Any real TTF renamed to simsun.ttf acts as "the user has the font".
        import shutil

        shutil.copy(f.bundled_font_dir() / f.BUNDLED_FONT_NAME, user_dir / "simsun.ttf")
        info = f.setup(user_dirs=(str(user_dir),))
        cache = ez_fonts.font_manager._font_cache
        assert "simsun.ttf" in cache
        # resolved to the USER's file, not registered as an alias of bundled
        assert cache["simsun.ttf"].file_path.parent == user_dir
        assert "simsun.ttf" not in info["aliases_registered"]

    def test_setup_idempotent(self, clean_machine):
        a = f.setup()
        b = f.setup()
        assert a is b  # cached per-process for the same dirs


class TestCjkRendering:
    """End-to-end: CJK text with a MISSING font must still render glyphs."""

    def _make_cjk_dxf(self, path, font_name: str):
        doc = ezdxf.new("R2010")
        style = doc.styles.add("HZ", font=font_name)
        msp = doc.modelspace()
        msp.add_text(
            "中文测试字体替换", height=20, dxfattribs={"style": "HZ"}
        ).set_placement((10, 10), align=TextEntityAlignment.LEFT)
        doc.saveas(str(path))

    def _text_ink(self, pdf_path) -> float:
        import pymupdf
        import numpy as np
        from PIL import Image

        d = pymupdf.open(str(pdf_path))
        try:
            pix = d[0].get_pixmap(dpi=72)
            arr = np.asarray(
                Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            ).astype(int)
            return float((arr.min(axis=2) < 245).mean())
        finally:
            d.close()

    def test_missing_ttf_substituted_renders_cjk(self, clean_machine, tmp_path):
        from dwg2pdf.options import ConvertOptions
        from dwg2pdf.renderer import render_dxf_to_pdf

        src = tmp_path / "cjk.dxf"
        self._make_cjk_dxf(src, "simsun.ttf")  # not installed on clean machine
        out = tmp_path / "cjk.pdf"
        render_dxf_to_pdf(src, out, ConvertOptions(layout="modelspace"))
        ink = self._text_ink(out)
        # 8 CJK glyphs at height 20 must leave substantial ink on a small page
        assert ink > 0.15, f"CJK glyphs missing (ink={ink:.3f}; tofu baseline ~0.02, real CJK ~0.38)"

    def test_missing_shx_substituted_renders_cjk(self, clean_machine, tmp_path):
        from dwg2pdf.options import ConvertOptions
        from dwg2pdf.renderer import render_dxf_to_pdf

        src = tmp_path / "cjkshx.dxf"
        self._make_cjk_dxf(src, "hztxt.shx")
        out = tmp_path / "cjkshx.pdf"
        render_dxf_to_pdf(src, out, ConvertOptions(layout="modelspace"))
        ink = self._text_ink(out)
        assert ink > 0.15, f"SHX-substituted CJK glyphs missing (ink={ink:.3f}; tofu baseline ~0.02)"
