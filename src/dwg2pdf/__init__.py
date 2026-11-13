"""dwg2pdf — batch DWG/DXF to PDF converter.

Pipeline:
    DWG --(ODA File Converter | LibreDWG dwg2dxf)--> DXF --(ezdxf + PyMuPDF)--> PDF

DXF input skips the external converter step, so DXF files always work with no
extra system dependencies.
"""

from __future__ import annotations

__version__ = "0.3.0"

SUPPORTED_INPUT_EXTENSIONS = (".dwg", ".dxf")
