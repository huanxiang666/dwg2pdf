"""GUI smoke test — skipped without tkinter or a usable display.

Covers: widget construction, file list management, option building, and a
synchronous end-to-end conversion through the same code path the Convert
button triggers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import write_colored_dxf

pytest.importorskip("tkinter")

if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display available for GUI smoke test", allow_module_level=True)

from dwg2pdf.gui import App  # noqa: E402
from dwg2pdf.options import OptionError  # noqa: E402


@pytest.fixture
def app():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    try:
        instance = App(tk, ttk, filedialog, messagebox, ScrolledText)
    except tk.TclError as exc:  # headless session without X/Wayland access
        pytest.skip(f"cannot create Tk window: {exc}")
    instance.root.withdraw()
    yield instance
    try:
        instance.root.destroy()
    except Exception:
        pass


def test_gui_add_remove_files(app, tmp_path):
    a = write_colored_dxf(tmp_path / "a.dxf")
    b = write_colored_dxf(tmp_path / "b.dxf")
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    app._add_paths([a, b, txt])  # txt must be rejected with a log line
    assert len(app.files) == 2
    assert app.listbox.size() == 2
    app.listbox.selection_set(0)
    app.remove_selected()
    assert len(app.files) == 1


def test_gui_option_validation(app):
    app.output_mode_var.set("custom")
    app.output_dir_var.set("")
    with pytest.raises(OptionError):
        app._build_opts()
    app.output_dir_var.set("/tmp/whatever")
    opts = app._build_opts()
    assert opts.output_dir == "/tmp/whatever"
    assert opts.layout == "auto"


def test_gui_convert_worker_end_to_end(app, tmp_path):
    src = write_colored_dxf(tmp_path / "g.dxf")
    app._add_paths([src])
    app.output_mode_var.set("custom")
    app.output_dir_var.set(str(tmp_path / "out"))
    opts = app._build_opts()
    from dwg2pdf.batch import plan_targets

    plans = plan_targets([Path(f) for f in app.files], opts.output_dir)
    app._convert_worker(plans, opts)  # synchronous: fills app.q directly
    messages = []
    while not app.q.empty():
        messages.append(app.q.get_nowait())
    kinds = [m[0] for m in messages]
    assert "done" in kinds
    batch = messages[kinds.index("done")][1]
    assert len(batch.ok) == 1
    assert (tmp_path / "out" / "g.pdf").exists()
