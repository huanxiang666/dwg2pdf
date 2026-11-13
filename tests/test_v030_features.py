"""Tests for preview rendering, region export, sysinfo, updater, go_core and
the engine downloader (with a real local HTTP server supporting Range).
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import socket
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import ezdxf

from conftest import write_colored_dxf


# ---------------------------------------------------------------- preview
class TestPreview:
    def test_render_preview_and_mapping(self, tmp_path):
        import numpy as np

        from dwg2pdf.preview import PreviewRequest, render_preview

        src = write_colored_dxf(tmp_path / "p.dxf")
        pv = render_preview(src, "Model", PreviewRequest(max_px=800))
        assert pv.record_count > 0
        assert pv.width_px > 0 and pv.height_px > 0
        arr = np.asarray(pv.image)
        ink = float((arr.min(axis=2) < 245).mean())
        assert ink > 0.002, "preview rendered blank"

        # roundtrip: WCS -> pt -> px -> WCS
        from ezdxf.math import Vec3

        bx0, by0, bx1, by1 = pv.bbox_wcs
        mid = Vec3((bx0 + bx1) / 2, (by0 + by1) / 2, 0)
        fwd = pv.placement_matrix.transform(mid)
        back = pv.px_to_wcs(fwd.x * pv.px_per_pt, fwd.y * pv.px_per_pt)
        assert abs(back[0] - mid.x) < 0.01 and abs(back[1] - mid.y) < 0.01

        # corners must contain the bbox
        x0, y0 = pv.px_to_wcs(0, 0)
        x1, y1 = pv.px_to_wcs(pv.width_px, pv.height_px)
        assert min(x0, x1) <= bx0 + 1 and max(x0, x1) >= bx1 - 1
        assert min(y0, y1) <= by0 + 1 and max(y0, y1) >= by1 - 1

    def test_export_region_pdf(self, tmp_path):
        import pymupdf

        from dwg2pdf.preview import export_region_pdf, render_preview, PreviewRequest

        src = write_colored_dxf(tmp_path / "r.dxf")
        pv = render_preview(src, "Model", PreviewRequest(max_px=600))
        bx0, by0, bx1, by1 = pv.bbox_wcs
        out = tmp_path / "region.pdf"
        stats = export_region_pdf(src, "Model",
                                  (bx0 + (bx1 - bx0) * 0.2, by0 + (by1 - by0) * 0.2),
                                  (bx0 + (bx1 - bx0) * 0.8, by0 + (by1 - by0) * 0.8),
                                  out)
        assert stats["pages"] == 1
        d = pymupdf.open(str(out))
        try:
            assert d.page_count == 1
            assert len(d[0].get_drawings()) > 0
        finally:
            d.close()

    def test_export_empty_region_raises(self, tmp_path):
        from dwg2pdf.preview import export_region_pdf

        src = write_colored_dxf(tmp_path / "e.dxf")
        with pytest.raises(ValueError):
            export_region_pdf(src, "Model", (99999, 99999), (99999.5, 99999.5),
                              tmp_path / "e.pdf")


# ---------------------------------------------------------------- sysinfo
class TestSysinfo:
    def test_cpu_meter_range(self):
        from dwg2pdf.sysinfo import CpuMeter

        m = CpuMeter()
        first = m.sample()
        time.sleep(0.3)
        # burn a little CPU so the sample is non-trivial
        x = sum(i * i for i in range(200_000))
        val = m.sample()
        if val is not None:  # unsupported platforms return None
            assert 0.0 <= val <= 100.0

    def test_eta_estimator(self):
        from dwg2pdf.sysinfo import EtaEstimator, format_duration

        e = EtaEstimator()
        assert e.remaining(3) is None          # cold: no estimate
        e.record(10.0)
        e.record(20.0)
        assert e.remaining(2) == pytest.approx(30.0)  # mean 15 * 2
        assert e.remaining(0) == 0.0
        seeded = EtaEstimator(seed_seconds_per_mb=2.0)
        assert seeded.remaining(1, next_file_mb=5.0) == pytest.approx(10.0)
        assert format_duration(None) == "估算中…"
        assert format_duration(45) == "45秒"
        assert format_duration(125) == "2分05秒"
        assert format_duration(3700) == "1时01分"


# ---------------------------------------------------------------- updater
class TestUpdater:
    def test_semver_order(self):
        from dwg2pdf.updater import _semver

        assert _semver("0.2.0") < _semver("0.10.0") < _semver("1.0.0")
        assert _semver("v1.2.3") == (1, 2, 3)
        assert _semver("garbage") == (0, 0, 0)

    def test_gui_asset_regex_filters_go_train(self):
        from dwg2pdf.updater import GUI_ASSET_RE

        # GUI-train assets match
        assert GUI_ASSET_RE.match("dwg2pdf-0.2.0-windows-x64-setup.exe")
        assert GUI_ASSET_RE.match("dwg2pdf_0.2.0_amd64.deb")
        assert GUI_ASSET_RE.match("dwg2pdf-0.2.0-linux-x64.tar.gz")
        # Go-train assets must NOT match (they would mislead GUI users)
        assert not GUI_ASSET_RE.match("dwg2pdf-v1.1.1-linux-amd64.tar.gz")
        assert not GUI_ASSET_RE.match("dwg2pdf-v1.1.1-windows-amd64.zip")
        assert not GUI_ASSET_RE.match("dwg2pdf-v1.1.1-darwin-arm64.tar.gz")

    def test_check_handles_network_failure(self, monkeypatch):
        from dwg2pdf import updater

        def boom(url, timeout=15.0):
            raise OSError("network down")

        monkeypatch.setattr(updater, "_http_json", boom)
        info = updater.check_for_updates("0.3.0")
        assert info.error and "GitHub" in info.error
        assert not info.update_available


# ---------------------------------------------------------------- go_core
class TestGoCore:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX fake script")
    def test_find_via_env(self, tmp_path, monkeypatch):
        from dwg2pdf import go_core

        exe = tmp_path / "dwg2pdf-go"
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        monkeypatch.setenv("DWG2PDF_GO_CORE", str(exe))
        assert go_core.find_go_core() == str(exe)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX fake script")
    def test_convert_with_fake_go_core(self, tmp_path, monkeypatch):
        """A stub 'go' that emits a valid PDF header proves the plumbing:
        flag order, output relocation onto the exact target, %PDF check."""
        from dwg2pdf import go_core
        from dwg2pdf.options import ConvertOptions

        stub = tmp_path / "fake-go"
        stub.write_text(
            "#!/bin/sh\n"
            "# args: -o DIR ... -report REPORT INPUT\n"
            "out=''; rep=''; input=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    -o) out=\"$2\"; shift 2;;\n"
            "    -report) rep=\"$2\"; shift 2;;\n"
            "    *) input=\"$1\"; shift;;\n"
            "  esac\n"
            "done\n"
            "base=$(basename \"$input\"); base=${base%.*}\n"
            "printf '%%PDF-1.7 fake' > \"$out/$base.pdf\"\n"
            "echo '{\"results\":[{\"pages\":3}]}' > \"$rep\"\n",
        )
        stub.chmod(0o755)
        target = tmp_path / "sub" / "renamed-target.pdf"
        info = go_core.convert_with_go(tmp_path / "src.dxf", target,
                                       ConvertOptions(), go_bin=str(stub))
        assert target.is_file()
        assert target.read_bytes().startswith(b"%PDF-")
        assert info["pages"] == 3
        assert not (target.parent / "src.pdf").exists()  # relocated

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX")
    def test_convert_failure_raises(self, tmp_path):
        from dwg2pdf import go_core
        from dwg2pdf.options import ConvertOptions

        with pytest.raises(RuntimeError):
            go_core.convert_with_go(tmp_path / "x.dxf", tmp_path / "x.pdf",
                                    ConvertOptions(), go_bin="/bin/false")


# ------------------------------------------------------- engine_download
class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Static file server that honors Range (needed by the downloader)."""

    payload_dir: Path = None  # set by the fixture

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        name = self.path.lstrip("/").split("?")[0]
        path = self.payload_dir / name
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            spec = rng[6:]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            end = min(end, len(data) - 1)
            chunk = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


@pytest.fixture
def http_server(tmp_path):
    payload = tmp_path / "srv"
    payload.mkdir()
    # deterministic 5 MB payload
    blob = bytes(range(256)) * 20480
    (payload / "engine.zip").write_bytes(blob)

    handler = type("H", (_RangeHandler,), {"payload_dir": payload})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", blob, payload
    srv.shutdown()


class TestEngineDownload:
    def test_download_multithread_matches_source(self, http_server, tmp_path):
        from dwg2pdf.engine_download import DownloadProgress, download_file

        base, blob, _ = http_server
        dest = tmp_path / "dl" / "engine.zip"
        seen = []
        download_file(f"{base}/engine.zip", dest, threads=4,
                      progress_cb=lambda p: seen.append(p.done_bytes))
        assert dest.is_file()
        assert hashlib.sha256(dest.read_bytes()).digest() == hashlib.sha256(blob).digest()
        assert seen and seen[-1] == len(blob)

    def test_resume_after_cancel(self, http_server, tmp_path):
        from dwg2pdf.engine_download import download_file

        base, blob, _ = http_server
        dest = tmp_path / "dl2" / "engine.zip"
        cancel = threading.Event()
        cancel.set()  # cancel immediately
        with pytest.raises(RuntimeError, match="取消"):
            download_file(f"{base}/engine.zip", dest, threads=4, cancel_event=cancel)
        assert dest.with_name(dest.name + ".part").exists()
        assert dest.with_name(dest.name + ".part.meta.json").exists()
        # second attempt (no cancel) resumes and completes
        download_file(f"{base}/engine.zip", dest, threads=4)
        assert hashlib.sha256(dest.read_bytes()).digest() == hashlib.sha256(blob).digest()

    def test_install_go_core_tar(self, tmp_path):
        from dwg2pdf import engine_download as ed

        src = tmp_path / "go.tar.gz"
        inner = tmp_path / "dwg2pdf"
        inner.write_bytes(b"\x7fELF-fake-binary")
        with tarfile.open(src, "w:gz") as tf:
            tf.add(inner, arcname="dwg2pdf-v9.9.9-linux-amd64/dwg2pdf")
        fake_root = tmp_path / "engine-home"
        orig = ed.user_engine_dir
        ed.user_engine_dir = lambda: fake_root
        try:
            exe = ed.install_archive("go-core", src)
        finally:
            ed.user_engine_dir = orig
        assert exe.is_file() and os.access(exe, os.X_OK)
        assert exe.read_bytes() == b"\x7fELF-fake-binary"

    def test_install_libredwg_zip(self, tmp_path):
        from dwg2pdf import engine_download as ed

        src = tmp_path / "lib.zip"
        with zipfile.ZipFile(src, "w") as zf:
            for name in ed.LIBREDWG_WIN_FILES:
                zf.writestr(f"libredwg-0.14/{name}", b"MZ-fake-" + name.encode())
        fake_root = tmp_path / "engine-home2"
        orig = ed.user_engine_dir
        ed.user_engine_dir = lambda: fake_root
        try:
            exe = ed.install_archive("libredwg-win64", src)
        finally:
            ed.user_engine_dir = orig
        assert exe.name == "dwg2dxf.exe" and exe.is_file()
        for name in ed.LIBREDWG_WIN_FILES:
            assert (exe.parent / name).is_file()

    def test_speed_test_marks_reachable(self, http_server):
        from dwg2pdf.engine_download import DownloadSource, speed_test

        base, _, _ = http_server
        srcs = [
            DownloadSource(id="ok", label="ok", url=f"{base}/engine.zip", kind="x"),
            DownloadSource(id="bad", label="bad", url=f"{base}/missing.zip", kind="x"),
            DownloadSource(id="page", label="page", url="https://example.com", kind="browser"),
        ]
        out = speed_test(srcs, timeout=5)
        by_id = {s.id: s for s in out}
        assert by_id["ok"].reachable is True and by_id["ok"].speed_kbps > 0
        assert by_id["bad"].reachable is False
        assert out[0].id == "ok"          # fastest first
        assert out[-1].id == "page"       # browser sources last


# ------------------------------------------------------------ gui preview
@pytest.fixture
def gui_app():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("no display")
    from dwg2pdf.gui import App

    try:
        app = App(tk, ttk, filedialog, messagebox, ScrolledText)
    except tk.TclError as exc:
        pytest.skip(f"cannot create Tk window: {exc}")
    app.root.withdraw()
    yield app
    try:
        app.root.destroy()
    except Exception:
        pass


class TestGuiPreviewFlow:
    def test_show_preview_and_canvas_ops(self, gui_app, tmp_path):
        """Feed a real PreviewImage through _show_preview, then exercise
        zoom/pan/region event handlers with synthetic events."""
        from dwg2pdf.preview import PreviewRequest, render_preview

        src = write_colored_dxf(tmp_path / "g.dxf")
        pv = render_preview(src, "Model", PreviewRequest(max_px=600))
        gui_app.root.update_idletasks()
        gui_app._show_preview(str(src), pv, str(src), None)
        assert gui_app._pv is pv
        gui_app.root.update_idletasks()

        ev = lambda **kw: SimpleNamespace(x=kw.get("x", 50), y=kw.get("y", 50),
                                          delta=kw.get("delta", 0), num=kw.get("num", 0))
        s0 = gui_app._view_scale
        gui_app._on_wheel(ev(x=100, y=100, delta=120))
        assert gui_app._view_scale > s0
        gui_app._on_wheel(ev(x=100, y=100, delta=-120))
        gui_app._on_pan_start(ev(x=10, y=10))
        gui_app._on_pan_move(ev(x=40, y=25))
        gui_app._on_pan_end(ev())

        # region mode → rubber band → export via stubbed save dialog.
        # The window is withdrawn (canvas ~1x1), so pin the view transform:
        # canvas px == image px, no pan.
        gui_app._view_scale = 1.0
        gui_app._view_off = [0.0, 0.0]
        gui_app.toggle_region_mode()
        assert gui_app._region_mode
        out = tmp_path / "region-out.pdf"
        gui_app._filedialog = SimpleNamespace(
            asksaveasfilename=lambda **kw: str(out),
            askdirectory=lambda **kw: "",
            askopenfilenames=lambda **kw: (),
        )
        # a band across the middle of the 600px preview of a drawing that
        # fills the frame → guaranteed to contain geometry
        w, h = gui_app._pv.width_px, gui_app._pv.height_px
        x0, y0, x1, y1 = int(w*0.2), int(h*0.2), int(w*0.8), int(h*0.8)
        gui_app._on_left_press(ev(x=x0, y=y0))
        gui_app._on_left_move(ev(x=x1, y=y1))
        gui_app._on_left_release(ev(x=x1, y=y1))
        # worker thread posts to q; drain with a bounded wait
        deadline = time.time() + 30
        got = None
        while time.time() < deadline:
            try:
                msg = gui_app.q.get_nowait()
            except Exception:
                msg = None
            if msg and msg[0] in ("region_done", "region_error"):
                got = msg
                break
            time.sleep(0.1)
        assert got is not None, "region export produced no message"
        assert got[0] == "region_done", got
        assert out.is_file() and out.read_bytes()[:5] == b"%PDF-"
        gui_app.toggle_region_mode()
        assert not gui_app._region_mode

    def test_build_opts_includes_core_and_quality(self, gui_app):
        gui_app.quality_var.set("high — 质量版（归档）")
        gui_app.core_var.set("auto — 内置渲染器（稳）")
        gui_app.threads_var.set(3)
        opts = gui_app._build_opts()
        assert opts.quality == "high"
        assert opts.core == "auto"
        assert opts.threads == 3
