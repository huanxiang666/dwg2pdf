"""Tkinter GUI for dwg2pdf (entry point: dwg2pdf-gui / dwg2pdf --gui).

v0.3.0 "看图软件" experience goals (users are CAD beginners):
- ttkbootstrap theming when available (graceful native-ttk fallback)
- drawing PREVIEW with wheel zoom / right-drag pan (custom Pillow replay
  backend, see preview.py) — like a CAD viewer
- rubber-band REGION EXPORT: drag a rectangle on the preview, export that
  window as a vector PDF, then open / print / reveal it
- ENGINE ASSISTANT: multi-source speed-tested multi-threaded download of the
  LibreDWG engine (Windows) and the optional Go fast core, with resume
- manual GitHub update check (never automatic)
- status bar: file progress + chunk progress + ETA + live CPU% + engine badge

Threading rule (unchanged): every heavy operation runs in a worker thread and
only posts messages to ``self.q``; Tk widgets are touched exclusively from
the main loop via ``_poll_queue``.
"""

from __future__ import annotations

import io
import multiprocessing
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from . import __version__
from .batch import collect_inputs, plan_targets, run_batch
from .dwg_backend import detect_converter
from .options import PAPER_CHOICES, ConvertOptions, OptionError
from .sysinfo import CpuMeter, EtaEstimator, format_duration

_LAYOUT_CHOICES = ["auto — 图纸布局优先", "modelspace — 模型空间", "all — 全部布局"]
_LAYOUT_VALUES = ["auto", "modelspace", "all"]
_QUALITY_CHOICES = ["fast — 效率版（预览/批量）", "balanced — 标准（默认）", "high — 质量版（归档）"]
_QUALITY_VALUES = ["fast", "balanced", "high"]
_CORE_CHOICES = ["auto — 内置渲染器（稳）", "go — Go 高速核心（实验，需下载）"]
_CORE_VALUES = ["auto", "go"]
_PAPER_CHOICES_CN = ["auto — 自动/图纸原尺寸"] + list(PAPER_CHOICES[1:])
_POLL_MS = 100
_BOLD_FONT = ("Helvetica", 11, "bold")
_TITLE_FONT = ("Helvetica", 13, "bold")


def _open_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _try_themify(root):
    """Apply a ttkbootstrap theme to an existing Tk root; None if unavailable.

    Returns the ttkbootstrap module (so widgets can pass bootstyles) or None.
    Every failure path degrades to native ttk — the GUI must never die
    because of theming.

    ttkbootstrap keeps a process-wide Style SINGLETON bound to the first Tk
    root. When a previous root was destroyed (e.g. an earlier App instance in
    the same process), the stale singleton raises TclError on any scaling
    access, so probe it and reset ``Style.instance`` before building a fresh
    one bound to the current root.
    """
    try:
        import ttkbootstrap as tb
    except Exception:
        return None
    style_cls = getattr(tb, "Style", None)
    if style_cls is None:
        return None
    old = getattr(style_cls, "instance", None)
    if old is not None:
        try:
            old_root = getattr(old, "root", None) or getattr(old, "master", None)
            old_root.tk.call("tk", "scaling")
        except Exception:
            try:
                style_cls.instance = None
            except Exception:
                return None
    for theme in ("cosmo", "flatly", "bootstrap-light", None):
        try:
            if theme:
                style_cls(theme=theme)
            else:
                style_cls()
            inst = style_cls.get_instance()
            probe = getattr(inst, "root", None) or root
            probe.tk.call("tk", "scaling")
            # A scaling call is not enough: theme definition modules may be
            # missing in a frozen build (dynamic imports), which only explodes
            # when the FIRST themed widget is built. Probe exactly that.
            probe_w = tb.Frame(root, bootstyle="primary")
            probe_w.destroy()
            return tb
        except Exception:
            continue
    return None


_TB_WIDGETS = ("Button", "Frame", "Label", "Combobox", "Checkbutton", "Radiobutton",
               "Spinbox", "Entry", "Labelframe", "Progressbar", "Separator",
               "Scrollbar", "Treeview", "Style")


def _resolve_widget_ns(ttk, tb):
    """Pick the widget namespace: ttkbootstrap when it exports every widget we
    use (bootstyle-capable), else the plain ttk module (no bootstyle!).

    bootstyle on a STANDARD ttk widget raises TclError: unknown option, so
    _bs() must only be used together with the ttkbootstrap namespace.
    """
    if tb is not None and all(hasattr(tb, n) for n in _TB_WIDGETS):
        import types

        ns = types.SimpleNamespace(**{n: getattr(tb, n) for n in _TB_WIDGETS})
        # ttkbootstrap 2.x does not export PanedWindow; the plain one is fine
        ns.PanedWindow = ttk.PanedWindow
        return ns, True
    return ttk, False


class App:
    """Main window.

    Constructor signature is part of the test contract:
    ``App(tk, ttk, filedialog, messagebox, ScrolledText)``.
    """

    def __init__(self, tk, ttk, filedialog, messagebox, ScrolledText):
        self._tk_mods = (tk, ttk, filedialog, messagebox, ScrolledText)
        self._tk, self._ttk = tk, ttk
        self._filedialog, self._messagebox = filedialog, messagebox
        self._ScrolledText = ScrolledText
        self.tb = None  # ttkbootstrap module or None

        self.files: list[str] = []
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._running = False
        self._chunk_note = ""
        self._last_output_dir: str | None = None
        self._start_ts = 0.0
        self._eta = EtaEstimator()

        # preview state
        self._pv = None                    # PreviewImage
        self._pv_source: str | None = None
        self._pv_dxf: str | None = None
        self._pv_tmpdir: str | None = None  # temp DXF dir for DWG previews
        self._pv_busy = False
        self._photo = None                  # keep ImageTk reference alive
        self._view_scale = 1.0
        self._view_off = [0.0, 0.0]         # image px at canvas origin
        self._panning = None
        self._region_mode = False
        self._rubber = None                 # (x0,y0) canvas press point
        self._rubber_rect = None            # canvas item id
        self._pv_hint = None

        self.root = tk.Tk()
        self.root.title(f"dwg2pdf {__version__} — DWG/DXF 批量转 PDF")
        self.root.geometry("1280x800")
        self.root.minsize(980, 620)
        self.tb = _try_themify(self.root)
        self._w, self._use_bs = _resolve_widget_ns(ttk, self.tb)

        self._apply_fallback_style()
        self._build_ui()
        self._start_cpu_meter()
        self._refresh_engine_badge()
        self.root.after(_POLL_MS, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ style
    def _apply_fallback_style(self):
        """Native ttk polish when ttkbootstrap is unavailable."""
        if self.tb is not None:
            return
        style = self._ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TButton", padding=5)
        style.configure("TLabelframe", padding=6)

    def _bs(self, style: str = "") -> dict:
        """bootstyle kwarg — ONLY valid on ttkbootstrap widgets (see
        _resolve_widget_ns); never pass it to plain ttk widgets."""
        if self._use_bs and style:
            return {"bootstyle": style}
        return {}

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        tk, ttk = self._tk, self._w

        # --- toolbar ---------------------------------------------------
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="dwg2pdf", font=_TITLE_FONT).pack(side="left")
        ttk.Label(bar, text=f" v{__version__}", foreground="#666").pack(side="left", padx=(0, 18))

        def btn(text, cmd, style="", parent=None):
            b = ttk.Button(parent or bar, text=text, command=cmd, **self._bs(style))
            b.pack(side="left", padx=3)
            return b

        btn("＋ 添加文件", self.add_files, "primary")
        btn("＋ 添加文件夹", self.add_folder)
        btn("－ 移除选中", self.remove_selected)
        self.convert_btn = btn("▶ 开始转换", self.start_convert, "success")
        self._preview_btn = btn("👁 预览图纸", self.preview_selected, "info")
        self._region_btn = btn("✂ 框选导出", self.toggle_region_mode, "warning")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        btn("⚙ 引擎助手", self.open_engine_dialog)
        btn("🔄 检查更新", self.check_update)
        btn("📁 输出目录", self.open_output)
        btn("✔ 环境检查", self.show_check)

        # --- main 3-pane ------------------------------------------------
        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        # left: file list
        left = ttk.Labelframe(main, text=" 文件列表 ", padding=6)
        main.add(left, weight=1)
        listf = ttk.Frame(left)
        listf.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(listf, selectmode="extended", activestyle="none",
                                  highlightthickness=1, highlightbackground="#c8c8c8")
        sb = ttk.Scrollbar(listf, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self.preview_selected())
        lbtns = ttk.Frame(left)
        lbtns.pack(fill="x", pady=(6, 0))
        ttk.Button(lbtns, text="清空", command=self.clear_files, width=8).pack(side="left")
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(lbtns, text="含子文件夹", variable=self.recursive_var).pack(side="left", padx=8)
        self._file_count_var = tk.StringVar(value="0 个文件")
        ttk.Label(lbtns, textvariable=self._file_count_var, foreground="#666").pack(side="right")

        # middle: preview canvas
        mid = ttk.Labelframe(main, text=" 预览（滚轮缩放 · 右键拖动平移 · 双击左侧文件加载） ", padding=2)
        main.add(mid, weight=3)
        self.canvas = tk.Canvas(mid, bg="#e9ecef", highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_canvas())
        self._bind_canvas_events()
        self._pv_hint = self.canvas.create_text(
            14, 14, anchor="nw", justify="left", fill="#868e96",
            text="预览使用：\n\n  1. 左侧选中一个 DWG/DXF 文件\n"
                 "  2. 点「👁 预览图纸」（或双击文件）\n"
                 "  3. 滚轮缩放，按住右键拖动平移\n\n"
                 "框选导出（打印局部详图）：\n\n  点「✂ 框选导出」进入选区模式，\n"
                 "  在图上拖出矩形 → 选择保存位置 →\n"
                 "  得到该区域的矢量 PDF，可直接打印。",
        )

        # right: options
        right = ttk.Labelframe(main, text=" 转换选项 ", padding=8)
        main.add(right, weight=1)
        self._build_options_panel(right, self._w)

        # --- status bar ---------------------------------------------------
        status = ttk.Frame(self.root, padding=(10, 4))
        status.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100,
                                        **self._bs("success-striped"))
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, width=44, anchor="e").pack(side="left", padx=(0, 8))
        self.cpu_var = tk.StringVar(value="CPU —")
        ttk.Label(status, textvariable=self.cpu_var, width=8, anchor="e",
                  foreground="#555").pack(side="left")
        self.engine_var = tk.StringVar(value="引擎 …")
        ttk.Label(status, textvariable=self.engine_var, width=20,
                  anchor="e", foreground="#555").pack(side="left", padx=(6, 0))

        # --- log ------------------------------------------------------------
        logf = ttk.Labelframe(self.root, text=" 日志 ", padding=4)
        logf.pack(fill="x", side="bottom", padx=10, pady=(0, 4))
        self.log = self._ScrolledText(logf, height=6, state="disabled", wrap="word")
        self.log.pack(fill="x")

    def _build_options_panel(self, parent, ttk):
        tk = self._tk

        def row(label_text):
            f = ttk.Frame(parent)
            f.pack(fill="x", pady=3)
            ttk.Label(f, text=label_text, width=9).pack(side="left")
            return f

        f = row("质量档:")
        self.quality_var = tk.StringVar(value=_QUALITY_CHOICES[1])
        ttk.Combobox(f, textvariable=self.quality_var, values=_QUALITY_CHOICES,
                     state="readonly", width=19).pack(side="left", fill="x", expand=True)

        f = row("渲染核心:")
        self.core_var = tk.StringVar(value=_CORE_CHOICES[0])
        ttk.Combobox(f, textvariable=self.core_var, values=_CORE_CHOICES,
                     state="readonly", width=19).pack(side="left", fill="x", expand=True)

        f = row("布局:")
        self.layout_var = tk.StringVar(value=_LAYOUT_CHOICES[0])
        ttk.Combobox(f, textvariable=self.layout_var, values=_LAYOUT_CHOICES,
                     state="readonly", width=19).pack(side="left", fill="x", expand=True)

        f = row("纸张:")
        self.paper_var = tk.StringVar(value=_PAPER_CHOICES_CN[0])
        ttk.Combobox(f, textvariable=self.paper_var, values=_PAPER_CHOICES_CN,
                     state="readonly", width=19).pack(side="left", fill="x", expand=True)

        f = row("")
        self.mono_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="单色输出（黑白打印）", variable=self.mono_var).pack(side="left")

        f = row("并行:")
        ttk.Label(f, text="文件").pack(side="left")
        self.jobs_var = tk.IntVar(value=0)
        ttk.Spinbox(f, from_=0, to=max(16, (os.cpu_count() or 4) * 2),
                    textvariable=self.jobs_var, width=4).pack(side="left", padx=(2, 8))
        ttk.Label(f, text="大图线程").pack(side="left")
        self.threads_var = tk.IntVar(value=0)
        ttk.Spinbox(f, from_=0, to=max(16, (os.cpu_count() or 4) * 2),
                    textvariable=self.threads_var, width=4).pack(side="left", padx=(2, 0))
        ttk.Label(parent, text="（0 = 自动按 CPU 核数）", foreground="#888").pack(anchor="e")

        f = row("输出到:")
        self.output_mode_var = tk.StringVar(value="source")
        ttk.Radiobutton(f, text="源文件同目录", variable=self.output_mode_var,
                        value="source", command=self._update_output_state).pack(side="left")
        ttk.Radiobutton(f, text="指定目录", variable=self.output_mode_var,
                        value="custom", command=self._update_output_state).pack(side="left", padx=(8, 0))
        f2 = ttk.Frame(parent)
        f2.pack(fill="x", pady=(0, 3))
        self.output_dir_var = tk.StringVar(value="")
        self._output_entry = ttk.Entry(f2, textvariable=self.output_dir_var, width=22)
        self._output_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(f2, text="浏览…", command=self.choose_output_dir, width=7).pack(side="left", padx=(4, 0))
        self._update_output_state()

        f = row("字体库:")
        self.font_dirs_var = tk.StringVar(value="")
        ttk.Entry(f, textvariable=self.font_dirs_var, width=16).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="添加…", command=self.add_font_dir, width=7).pack(side="left", padx=(4, 0))
        ttk.Label(parent, text="已内置中文字体，缺失自动替换；\n可添加自己的 SHX/TTF 字体目录",
                  foreground="#888", justify="left").pack(anchor="w", pady=(0, 4))

        self.skip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="跳过已存在的 PDF", variable=self.skip_var).pack(anchor="w", pady=(4, 0))

        card = ttk.Frame(parent, padding=6)
        card.pack(fill="x", pady=(10, 0))
        self.engine_detail_var = tk.StringVar(value="正在检测引擎…")
        ttk.Label(card, textvariable=self.engine_detail_var, wraplength=230,
                  justify="left", foreground="#444").pack(side="left", fill="x", expand=True)

    def _update_output_state(self):
        state = "normal" if self.output_mode_var.get() == "custom" else "disabled"
        try:
            self._output_entry.configure(state=state)
        except Exception:
            pass

    # ------------------------------------------------------------- file list
    def _refresh_listbox(self) -> None:
        self.listbox.delete(0, self._tk.END)
        for f in self.files:
            self.listbox.insert(self._tk.END, os.path.basename(f))
        self._file_count_var.set(f"{len(self.files)} 个文件")

    def add_files(self) -> None:
        paths = self._filedialog.askopenfilenames(
            title="选择 CAD 图纸",
            filetypes=[("CAD 图纸", "*.dwg *.dxf *.DWG *.DXF"), ("所有文件", "*.*")])
        if paths:
            self._add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        folder = self._filedialog.askdirectory(title="选择包含图纸的文件夹")
        if not folder:
            return
        files, ignored = collect_inputs([folder], recursive=self.recursive_var.get())
        for path in ignored:
            self._log(f"提示: {path} 中未找到 CAD 文件")
        self._add_paths(files)

    def _add_paths(self, paths) -> None:
        existing = set(self.files)
        added = 0
        for p in paths:
            p = Path(p)
            if p.suffix.lower() not in (".dwg", ".dxf"):
                self._log(f"忽略不支持的文件: {p.name}")
                continue
            s = str(p)
            if s not in existing:
                existing.add(s)
                self.files.append(s)
                added += 1
        if added:
            self._log(f"已添加 {added} 个文件")
        self._refresh_listbox()

    def remove_selected(self) -> None:
        for idx in reversed(self.listbox.curselection()):
            del self.files[idx]
        self._refresh_listbox()

    def clear_files(self) -> None:
        self.files.clear()
        self._refresh_listbox()

    def choose_output_dir(self) -> None:
        folder = self._filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.output_dir_var.set(folder)
            self.output_mode_var.set("custom")
            self._update_output_state()

    def add_font_dir(self) -> None:
        """Append a user font library dir (TTF/OTF/SHX/SHP/LFF, recursive)."""
        folder = self._filedialog.askdirectory(title="选择字体库目录（含 SHX/TTF 等 CAD 字体）")
        if not folder:
            return
        current = [d.strip() for d in self.font_dirs_var.get().split(os.pathsep) if d.strip()]
        if folder not in current:
            current.append(folder)
        self.font_dirs_var.set(os.pathsep.join(current))
        self._log(f"已添加字体库: {folder}")

    # ----------------------------------------------------------------- opts
    def _build_opts(self) -> ConvertOptions:
        layout = _LAYOUT_VALUES[_LAYOUT_CHOICES.index(self.layout_var.get())]
        quality = _QUALITY_VALUES[_QUALITY_CHOICES.index(self.quality_var.get())]
        core = _CORE_VALUES[_CORE_CHOICES.index(self.core_var.get())]
        paper = self.paper_var.get().split(" — ")[0].strip()
        output_dir = None
        if self.output_mode_var.get() == "custom":
            output_dir = self.output_dir_var.get().strip()
            if not output_dir:
                raise OptionError("已选择“指定目录”，但未填写目录")
        font_dirs = tuple(
            d.strip() for d in self.font_dirs_var.get().split(os.pathsep) if d.strip()
        )
        return ConvertOptions(
            layout=layout,
            monochrome=self.mono_var.get(),
            paper=paper,
            jobs=self.jobs_var.get(),
            output_dir=output_dir,
            skip_existing=self.skip_var.get(),
            font_dirs=font_dirs,
            threads=self.threads_var.get(),
            quality=quality,
            core=core,
        ).validate()

    # -------------------------------------------------------------- convert
    def start_convert(self) -> None:
        if self._running:
            return
        if not self.files:
            self._messagebox.showwarning("dwg2pdf", "请先添加 .dwg / .dxf 文件")
            return
        try:
            opts = self._build_opts()  # read widgets on the UI thread only
        except OptionError as exc:
            self._messagebox.showerror("dwg2pdf", str(exc))
            return
        if opts.core == "go":
            from . import go_core

            if go_core.find_go_core() is None:
                go_now = self._messagebox.askyesno(
                    "Go 核心未安装",
                    "尚未下载 Go 高速核心。\n\n"
                    "「是」现在打开引擎助手下载；\n「否」本次用内置渲染器转换。")
                if go_now:
                    self.open_engine_dialog()
                    return
                opts = ConvertOptions(**{**opts.__dict__, "core": "auto"})
        files = [Path(f) for f in self.files]
        if opts.output_dir:
            Path(opts.output_dir).expanduser().mkdir(parents=True, exist_ok=True)
        self._last_output_dir = opts.output_dir or str(files[0].parent)
        plans = plan_targets(files, opts.output_dir)
        self._running = True
        self._start_ts = time.monotonic()
        self._eta = EtaEstimator()
        self.convert_btn.configure(state="disabled")
        self.progress.configure(maximum=len(plans), value=0)
        self.status_var.set(f"准备转换 {len(plans)} 个文件…")
        self._log(f"开始转换 {len(plans)} 个文件 → {opts.output_dir or '源文件目录'}"
                  f"（核心 {opts.core}，质量 {opts.quality}）")
        threading.Thread(target=self._convert_worker, args=(plans, opts), daemon=True).start()

    def _convert_worker(self, plans, opts: ConvertOptions) -> None:
        try:
            def progress_cb(result, done: int, total: int) -> None:
                self.q.put(("progress", result, done, total))

            def chunk_cb(name: str, done: int, total: int) -> None:
                self.q.put(("chunk", name, done, total))

            batch = run_batch(plans, opts, progress_cb=progress_cb, chunk_progress_cb=chunk_cb)
            self.q.put(("done", batch))
        except Exception as exc:  # surface any unexpected crash to the UI
            self.q.put(("error", f"{exc.__class__.__name__}: {exc}\n"
                                 f"{traceback.format_exc(limit=3)}"))

    # -------------------------------------------------------------- preview
    def preview_selected(self) -> None:
        if self._pv_busy:
            self._log("预览正在生成中，请稍候…")
            return
        sel = self.listbox.curselection()
        if not sel:
            if len(self.files) == 1:
                sel = (0,)
            else:
                self._messagebox.showinfo("预览", "请先在左侧选中一个文件")
                return
        src = self.files[sel[0]]
        self._pv_busy = True
        self.status_var.set(f"正在生成预览：{os.path.basename(src)} …")
        self._log(f"预览：{src}")
        threading.Thread(target=self._preview_worker, args=(src,), daemon=True).start()

    def _preview_worker(self, src: str) -> None:
        try:
            from .preview import PreviewRequest, render_preview

            dxf_path = src
            tmpdir = None
            if src.lower().endswith(".dwg"):
                converter = detect_converter()
                if converter is None:
                    self.q.put(("preview_error",
                                "预览 DWG 需要转换引擎：请先点「⚙ 引擎助手」下载，"
                                "或安装 ODA File Converter（DXF 文件可直接预览）"))
                    return
                from .dwg_backend import convert_dwg_to_dxf

                tmpdir = tempfile.mkdtemp(prefix="dwg2pdf-preview-")
                dxf_path = str(Path(tmpdir) / (Path(src).stem + ".dxf"))
                convert_dwg_to_dxf(Path(src), Path(dxf_path), converter, timeout=600)
            pv = render_preview(dxf_path, "Model", PreviewRequest(max_px=2600))
            self.q.put(("preview", src, pv, dxf_path, tmpdir))
        except Exception as exc:
            self.q.put(("preview_error", f"{exc.__class__.__name__}: {exc}"))

    def _show_preview(self, src, pv, dxf_path, tmpdir) -> None:
        if self._pv_tmpdir and self._pv_tmpdir != tmpdir:
            self._cleanup_tmpdir(self._pv_tmpdir)
        self._pv, self._pv_source, self._pv_dxf = pv, src, dxf_path
        self._pv_tmpdir = tmpdir
        self._pv_busy = False
        if self._pv_hint:
            try:
                self.canvas.delete(self._pv_hint)
            except Exception:
                pass
            self._pv_hint = None
        self._fit_view()
        total = pv.record_seconds + pv.render_seconds
        self._log(f"预览就绪：{pv.width_px}x{pv.height_px}px，{pv.record_count} 图元，"
                  f"耗时 {total:.1f}s" + (f"，跳过 {pv.skipped_images} 个 OLE 图片对象"
                                          if pv.skipped_images else ""))
        self.status_var.set(f"预览：{os.path.basename(src)}（滚轮缩放 / 右键平移）")

    @staticmethod
    def _cleanup_tmpdir(path: str) -> None:
        try:
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass

    def _fit_view(self) -> None:
        if not self._pv:
            return
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        iw, ih = self._pv.width_px, self._pv.height_px
        self._view_scale = min(cw / iw, ch / ih) * 0.98
        self._view_off = [(iw - cw / self._view_scale) / 2,
                          (ih - ch / self._view_scale) / 2]
        self._redraw_canvas()

    def _redraw_canvas(self) -> None:
        if not self._pv:
            return
        try:
            from PIL import ImageTk
        except Exception:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 20 or ch < 20:
            return
        s = self._view_scale
        iw, ih = self._pv.width_px, self._pv.height_px
        x0, y0 = self._view_off
        cx0, cy0 = max(0, int(x0)), max(0, int(y0))
        cx1, cy1 = min(iw, int(x0 + cw / s) + 1), min(ih, int(y0 + ch / s) + 1)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        region = self._pv.image.crop((cx0, cy0, cx1, cy1))
        dw, dh = max(1, int((cx1 - cx0) * s)), max(1, int((cy1 - cy0) * s))
        if dw * dh > 40_000_000:  # safety valve against absurd upscales
            return
        resized = region.resize((dw, dh))
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("view")
        self.canvas.create_image(int((cx0 - x0) * s), int((cy0 - y0) * s),
                                 anchor="nw", image=self._photo, tags="view")

    def _bind_canvas_events(self) -> None:
        c = self.canvas
        c.bind("<MouseWheel>", self._on_wheel)          # win/mac
        c.bind("<Button-4>", self._on_wheel)            # linux scroll up
        c.bind("<Button-5>", self._on_wheel)            # linux scroll down
        c.bind("<ButtonPress-3>", self._on_pan_start)
        c.bind("<B3-Motion>", self._on_pan_move)
        c.bind("<ButtonRelease-3>", self._on_pan_end)
        c.bind("<ButtonPress-2>", self._on_pan_start)
        c.bind("<B2-Motion>", self._on_pan_move)
        c.bind("<ButtonRelease-2>", self._on_pan_end)
        c.bind("<ButtonPress-1>", self._on_left_press)
        c.bind("<B1-Motion>", self._on_left_move)
        c.bind("<ButtonRelease-1>", self._on_left_release)

    def _on_wheel(self, event) -> None:
        if not self._pv:
            return
        delta = getattr(event, "delta", 0)
        if delta:
            factor = 1.25 if delta > 0 else 0.8
        else:
            factor = 1.25 if getattr(event, "num", 4) == 4 else 0.8
        mx, my = event.x, event.y
        s_old = self._view_scale
        s_new = max(0.02, min(60.0, s_old * factor))
        # keep the image point under the cursor fixed
        ix = self._view_off[0] + mx / s_old
        iy = self._view_off[1] + my / s_old
        self._view_scale = s_new
        self._view_off = [ix - mx / s_new, iy - my / s_new]
        self._redraw_canvas()

    def _on_pan_start(self, event) -> None:
        self._panning = (event.x, event.y, self._view_off[0], self._view_off[1])
        self.canvas.configure(cursor="fleur")

    def _on_pan_move(self, event) -> None:
        if not self._panning or not self._pv:
            return
        x, y, ox, oy = self._panning
        self._view_off = [ox - (event.x - x) / self._view_scale,
                          oy - (event.y - y) / self._view_scale]
        self._redraw_canvas()

    def _on_pan_end(self, event) -> None:
        self._panning = None
        self.canvas.configure(cursor="crosshair" if self._region_mode else "hand2")

    # rubber-band region selection (left button while in region mode)
    def _on_left_press(self, event) -> None:
        if not self._region_mode or not self._pv:
            return
        self._rubber = (event.x, event.y)
        if self._rubber_rect:
            self.canvas.delete(self._rubber_rect)
        self._rubber_rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#e8590c", width=2, dash=(4, 2))

    def _on_left_move(self, event) -> None:
        if self._rubber is None or not self._rubber_rect:
            return
        x0, y0 = self._rubber
        self.canvas.coords(self._rubber_rect, x0, y0, event.x, event.y)

    def _on_left_release(self, event) -> None:
        if self._rubber is None or not self._pv:
            self._rubber = None
            return
        x0, y0 = self._rubber
        self._rubber = None
        x1, y1 = event.x, event.y
        if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
            if self._rubber_rect:
                self.canvas.delete(self._rubber_rect)
            self._rubber_rect = None
            self._log("选区太小，已取消（拖动出一个明显的矩形再松开）")
            return
        s = self._view_scale
        ax = self._view_off[0] + min(x0, x1) / s
        ay = self._view_off[1] + min(y0, y1) / s
        bx = self._view_off[0] + max(x0, x1) / s
        by = self._view_off[1] + max(y0, y1) / s
        self._ask_region_export(ax, ay, bx, by)

    def toggle_region_mode(self) -> None:
        if not self._pv:
            self._messagebox.showinfo("框选导出", "请先预览一个图纸，再进入框选模式")
            return
        self._region_mode = not self._region_mode
        self.canvas.configure(cursor="crosshair" if self._region_mode else "hand2")
        self._region_btn.configure(
            text="✂ 退出框选" if self._region_mode else "✂ 框选导出",
            **self._bs("warning-outline" if self._region_mode else "warning"))
        self._log("框选模式：在预览图上拖出矩形即可导出该区域" if self._region_mode
                  else "已退出框选模式")

    def _ask_region_export(self, ax, ay, bx, by) -> None:
        default_name = f"{Path(self._pv_source).stem}-区域.pdf"
        initialdir = self._last_output_dir or str(Path(self._pv_source).parent)
        out = self._filedialog.asksaveasfilename(
            title="导出选区为 PDF", defaultextension=".pdf",
            initialdir=initialdir, initialfile=default_name,
            filetypes=[("PDF 文件", "*.pdf")])
        if not out:
            if self._rubber_rect:
                self.canvas.delete(self._rubber_rect)
            self._rubber_rect = None
            return
        self._last_output_dir = str(Path(out).parent)
        try:
            p1 = self._pv.px_to_wcs(ax, ay)
            p2 = self._pv.px_to_wcs(bx, by)
        except Exception as exc:
            self._messagebox.showerror("坐标换算失败", str(exc))
            return
        self.status_var.set("正在导出所选区域…")
        self._log(f"区域导出：WCS ({p1[0]:.1f},{p1[1]:.1f})–({p2[0]:.1f},{p2[1]:.1f}) → {out}")
        threading.Thread(target=self._region_worker, args=(p1, p2, out), daemon=True).start()

    def _region_worker(self, p1, p2, out) -> None:
        try:
            from .preview import export_region_pdf

            stats = export_region_pdf(self._pv_dxf, "Model", p1, p2, out,
                                      monochrome=self.mono_var.get())
            self.q.put(("region_done", stats))
        except Exception as exc:
            self.q.put(("region_error", f"{exc.__class__.__name__}: {exc}"))

    # -------------------------------------------------------- engine helper
    def open_engine_dialog(self) -> None:
        try:
            dlg = EngineDialog(self.root, self._w, self._tk, self._messagebox,
                               self.tb if self._use_bs else None,
                               on_finished=self._refresh_engine_badge)
        except Exception as exc:
            self._messagebox.showerror("引擎助手", f"打开失败：{exc}")
            return
        try:
            dlg.win.grab_set()
        except Exception:
            pass

    def _refresh_engine_badge(self) -> None:
        threading.Thread(target=self._engine_badge_worker, daemon=True).start()

    def _engine_badge_worker(self) -> None:
        from . import go_core

        try:
            conv = detect_converter()
        except Exception:
            conv = None
        go = go_core.find_go_core()
        if conv:
            name = "ODA" if "oda" in conv.kind.lower() else "LibreDWG"
            text = f"DWG引擎 {name} ✓" + ("  Go✓" if go else "")
            detail = f"DWG 引擎：{conv.display_name}\n{conv.path}"
            if go:
                detail += f"\nGo 高速核心：{go}"
        else:
            text = "DWG引擎 未装 ⚠"
            detail = ("尚未找到 DWG 引擎——点「⚙ 引擎助手」一键下载。\n"
                      "DXF 文件不受影响，现在就能转。")
        self.q.put(("engine", text, detail))

    # --------------------------------------------------------------- update
    def check_update(self) -> None:
        self.status_var.set("正在检查更新…")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self) -> None:
        try:
            from .updater import check_for_updates

            info = check_for_updates(__version__)
            self.q.put(("update", info))
        except Exception as exc:
            self.q.put(("error", f"检查更新失败：{exc}"))

    # ------------------------------------------------------------------ cpu
    def _start_cpu_meter(self) -> None:
        def loop():
            meter = CpuMeter()
            meter.sample()  # prime
            while True:
                time.sleep(2.0)
                try:
                    pct = meter.sample()
                except Exception:
                    continue
                if pct is not None:
                    self.q.put(("cpu", pct))

        threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------- old misc
    def open_output(self) -> None:
        target = self._last_output_dir
        if not target and self.output_mode_var.get() == "custom":
            target = self.output_dir_var.get().strip()
        if not target and self.files:
            target = str(Path(self.files[0]).parent)
        if not target or not Path(target).exists():
            self._messagebox.showinfo("dwg2pdf", "还没有输出目录")
            return
        try:
            _open_in_file_manager(target)
        except Exception as exc:
            self._messagebox.showerror("dwg2pdf", f"无法打开目录: {exc}")

    def show_check(self) -> None:
        from .cli import run_check

        buf = io.StringIO()
        old = sys.stdout
        try:
            sys.stdout = buf
            run_check()
        finally:
            sys.stdout = old
        win = self._tk.Toplevel(self.root)
        win.title("环境检查")
        win.geometry("680x460")
        text = self._tk.Text(win, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", buf.getvalue())
        text.configure(state="disabled")

    # ---------------------------------------------------------------- queue
    def _poll_queue(self) -> None:
        # Live status line so long renders visibly progress.
        if self._running:
            elapsed = time.monotonic() - self._start_ts
            try:
                done = int(float(self.progress["value"] or 0))
                total = int(float(self.progress["maximum"] or 0))
            except (ValueError, TypeError):
                done, total = 0, 0
            rem = self._eta.remaining(max(0, total - done)) if total else None
            parts = [f"转换中… {done}/{total}{self._chunk_note}",
                     f"已用时 {format_duration(elapsed)}"]
            if total - done > 0:
                parts.append(f"预计剩余 {format_duration(rem)}")
            self.status_var.set("，".join(parts))
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, result, done, total = msg
                    self._chunk_note = ""
                    self.progress.configure(value=done)
                    if result.status == "ok":
                        self._eta.record(result.seconds)
                        self._log(f"✔ {Path(result.source).name} → "
                                  f"{Path(result.target).name}（{result.pages} 页, "
                                  f"{result.seconds:.1f}s）")
                        for warning in result.warnings:
                            self._log(f"  ⚠ {Path(result.source).name}: {warning}")
                    elif result.status == "skipped":
                        self._log(f"⊘ {Path(result.source).name} 已存在，跳过")
                    else:
                        first = result.error.splitlines()[0] if result.error else "未知错误"
                        self._log(f"✘ {Path(result.source).name}: {first}")
                elif kind == "chunk":
                    _, name, c_done, c_total = msg
                    self._chunk_note = f"（{Path(name).name[:18]} 分块 {c_done}/{c_total}）"
                elif kind == "done":
                    batch = msg[1]
                    self._running = False
                    self.convert_btn.configure(state="normal")
                    summary = (f"完成: {len(batch.ok)} 成功, {len(batch.failed)} 失败, "
                               f"{len(batch.skipped)} 跳过（{batch.total_seconds:.1f}s）")
                    self.status_var.set(summary)
                    self._log(summary)
                    if batch.failed:
                        self._messagebox.showwarning("dwg2pdf", summary + "\n失败详情见日志")
                    else:
                        if self._last_output_dir and self._messagebox.askyesno(
                                "转换完成", summary + "\n\n打开输出目录？"):
                            self.open_output()
                elif kind == "error":
                    self._running = False
                    self.convert_btn.configure(state="normal")
                    self.status_var.set("出错")
                    self._log(f"转换出错: {msg[1]}")
                    self._messagebox.showerror("dwg2pdf", msg[1][:600])
                elif kind == "preview":
                    _, src, pv, dxf_path, tmpdir = msg
                    self._show_preview(src, pv, dxf_path, tmpdir)
                elif kind == "preview_error":
                    self._pv_busy = False
                    self.status_var.set("预览失败")
                    self._log(f"预览失败：{msg[1]}")
                    self._messagebox.showerror("预览失败", msg[1])
                elif kind == "region_done":
                    stats = msg[1]
                    if self._rubber_rect:
                        self.canvas.delete(self._rubber_rect)
                    self._rubber_rect = None
                    self.status_var.set("区域导出完成")
                    self._log(f"区域导出完成：{stats['path']}（{stats['seconds']}s，"
                              f"裁剪后 {stats['records_after_crop']} 图元）")
                    self._region_result_dialog(stats["path"])
                elif kind == "region_error":
                    self.status_var.set("区域导出失败")
                    self._log(f"区域导出失败：{msg[1]}")
                    self._messagebox.showerror("区域导出失败", msg[1])
                elif kind == "cpu":
                    self.cpu_var.set(f"CPU {msg[1]:.0f}%")
                elif kind == "engine":
                    self.engine_var.set(msg[1])
                    self.engine_detail_var.set(msg[2])
                elif kind == "update":
                    self._show_update_dialog(msg[1])
                elif kind == "backend":  # legacy message, tolerate
                    self.engine_var.set(msg[1])
        except queue.Empty:
            pass
        self.root.after(_POLL_MS, self._poll_queue)

    def _region_result_dialog(self, path: str) -> None:
        from .preview import open_path, print_pdf

        dlg = self._tk.Toplevel(self.root)
        dlg.title("区域导出完成")
        dlg.resizable(False, False)
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        f = self._w.Frame(dlg, padding=16)
        f.pack()
        self._w.Label(f, text=f"已导出：{Path(path).name}", font=_BOLD_FONT).pack(anchor="w")
        self._w.Label(f, text=str(path), foreground="#666", wraplength=420).pack(anchor="w", pady=(2, 10))
        row = self._w.Frame(f)
        row.pack()

        def do_print():
            status = print_pdf(path)
            self._log(f"打印：{status}")
            dlg.destroy()

        self._w.Button(row, text="🖨 打印", command=do_print,
                       **self._bs("primary")).pack(side="left", padx=4)
        self._w.Button(row, text="打开 PDF",
                       command=lambda: (open_path(path), dlg.destroy())).pack(side="left", padx=4)
        self._w.Button(row, text="打开目录",
                       command=lambda: (self._open_folder(str(Path(path).parent)),
                                        dlg.destroy())).pack(side="left", padx=4)
        self._w.Button(row, text="关闭", command=dlg.destroy).pack(side="left", padx=4)
        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    @staticmethod
    def _open_folder(path: str) -> None:
        try:
            _open_in_file_manager(path)
        except Exception:
            pass

    def _show_update_dialog(self, info) -> None:
        dlg = self._tk.Toplevel(self.root)
        dlg.title("检查更新")
        dlg.resizable(False, False)
        f = self._w.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)
        if info.error:
            self._w.Label(f, text=f"检查失败：{info.error}", foreground="#c92a2a",
                          wraplength=400, justify="left").pack(anchor="w")
        elif info.update_available:
            self._w.Label(f, text=f"发现新版本 v{info.latest}（当前 v{info.current}）",
                          foreground="#2b8a3e", font=_BOLD_FONT).pack(anchor="w")
            if info.notes:
                self._w.Label(f, text=info.notes[:300], wraplength=400,
                              justify="left", foreground="#555").pack(anchor="w", pady=(6, 6))
            if info.url:
                self._w.Button(f, text="打开下载页",
                               command=lambda: webbrowser.open(info.url),
                               **self._bs("primary")).pack(anchor="w")
        else:
            self._w.Label(f, text=f"当前 v{info.current} 已是最新版本 ✓",
                          font=_BOLD_FONT).pack(anchor="w")
        self._w.Button(f, text="关闭", command=dlg.destroy).pack(anchor="e", pady=(12, 0))
        self.status_var.set("更新检查完成")

    # ---------------------------------------------------------------- utils
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("[%H:%M:%S] ") + text.rstrip() + "\n")
        try:
            if int(self.log.index("end-1c").split(".")[0]) > 800:
                self.log.delete("1.0", "200.0")
        except Exception:
            pass
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        if self._running:
            if not self._messagebox.askyesno("dwg2pdf", "正在转换中，确定要退出吗？"):
                return
            # A conversion is in flight on a daemon thread that may own a
            # ProcessPoolExecutor; its atexit join would keep this (now
            # windowless) process alive. User asked to abort → hard exit.
            self._cleanup_tmpdir(self._pv_tmpdir or "")
            self.root.destroy()
            os._exit(0)
        if self._pv_tmpdir:
            self._cleanup_tmpdir(self._pv_tmpdir)
        self.root.destroy()


class EngineDialog:
    """引擎助手：多源测速、多线程下载、自动装配 DWG 引擎 / Go 高速核心。"""

    def __init__(self, master, ttk, tk, messagebox, tb, on_finished=None):
        self._ttk, self._tk = ttk, tk
        self._messagebox = messagebox
        self._tb = tb
        self._on_finished = on_finished
        self._sources = []
        self._cancel = threading.Event()
        self._downloading = False

        self.win = tk.Toplevel(master)
        self.win.title("引擎助手 — 下载 DWG 转换引擎 / 高速核心")
        self.win.geometry("880x580")
        self.win.minsize(760, 480)

        from .engine_download import user_engine_dir

        top = ttk.Frame(self.win, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="为什么需要这一步？", font=_BOLD_FONT).pack(anchor="w")
        ttk.Label(top, wraplength=840, justify="left", foreground="#555",
                  text="DWG 是 Autodesk 私有格式，需要一个转换引擎才能打开。Linux 安装包已内置引擎，"
                       "通常无需下载；Windows 用户在这里一键下载官方 LibreDWG 引擎（免费开源）即可。"
                       "「Go 高速核心」是可选增强：DXF 转换提速数十倍且文字可搜索。"
                       "所有下载源均为官方发布地址或其加速镜像；多线程下载、断点续传。").pack(anchor="w", pady=(2, 6))
        self._status_var = tk.StringVar(value=f"安装目录：{user_engine_dir()}")
        ttk.Label(top, textvariable=self._status_var, foreground="#888").pack(anchor="w")

        cols = ("label", "size", "speed", "note")
        self.tree = ttk.Treeview(self.win, columns=cols, show="headings", height=9)
        for cid, text, w in (("label", "下载源", 400), ("size", "大小", 70),
                             ("speed", "实测速度", 100), ("note", "说明", 250)):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.tree.bind("<Double-1>", lambda e: self._download_selected())

        bar = ttk.Frame(self.win, padding=(10, 2))
        bar.pack(fill="x")
        self._test_btn = ttk.Button(bar, text="① 测速（自动选最快源）", command=self._speed_test)
        self._test_btn.pack(side="left")
        self._dl_btn = ttk.Button(bar, text="② 下载并安装选中项", command=self._download_selected)
        self._dl_btn.pack(side="left", padx=6)
        self._open_btn = ttk.Button(bar, text="打开官网（浏览器）", command=self._open_browser)
        self._open_btn.pack(side="left")
        self._cancel_btn = ttk.Button(bar, text="取消下载", command=self._cancel_download,
                                      state="disabled")
        self._cancel_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=self.win.destroy).pack(side="right")

        progf = ttk.Frame(self.win, padding=(10, 4))
        progf.pack(fill="x")
        self._prog = ttk.Progressbar(progf, mode="determinate", maximum=100)
        self._prog.pack(side="left", fill="x", expand=True)
        self._prog_var = tk.StringVar(value="就绪：先点「测速」，再双击或点「下载并安装」")
        ttk.Label(progf, textvariable=self._prog_var, width=50, anchor="e").pack(side="left", padx=(8, 0))

        self._load_sources()
        self._dl_queue: "queue.Queue" = queue.Queue()
        self.win.after(150, self._poll_dl)

    def _load_sources(self) -> None:
        from .engine_download import available_sources

        self._sources = available_sources()
        self.tree.delete(*self.tree.get_children())
        for s in self._sources:
            size = f"{s.size_mb:.0f}MB" if s.size_mb else "—"
            self.tree.insert("", "end", iid=s.id, values=(s.label, size, "未测速", s.note))

    def _selected_source(self):
        sel = self.tree.selection()
        if not sel:
            self._messagebox.showinfo("引擎助手", "请先在列表里选择一个下载源（建议先测速）",
                                      parent=self.win)
            return None
        sid = sel[0]
        return next((s for s in self._sources if s.id == sid), None)

    def _speed_test(self) -> None:
        self._test_btn.configure(state="disabled")
        self._prog_var.set("正在测速…")

        def run():
            from .engine_download import speed_test

            try:
                speed_test(self._sources, timeout=8)
            except Exception:
                pass
            self._dl_queue.put(("speed",))

        threading.Thread(target=run, daemon=True).start()

    def _download_selected(self) -> None:
        if self._downloading:
            return
        src = self._selected_source()
        if src is None:
            return
        if src.kind == "browser":
            webbrowser.open(src.url)
            self._prog_var.set("已在浏览器打开官网，安装后本软件会自动识别")
            return
        self._downloading = True
        self._cancel.clear()
        self._dl_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")

        def run():
            from .engine_download import download_file, install_archive, user_engine_dir

            try:
                dest = user_engine_dir() / "downloads" / Path(src.url.split("?")[0]).name
                last = [0.0]

                def cb(p):
                    now = time.time()
                    if now - last[0] >= 0.2 or p.finished:
                        last[0] = now
                        self._dl_queue.put(("prog", p.done_bytes, p.total_bytes,
                                            p.speed_bps, p.eta_seconds))

                download_file(src.url, dest, threads=4, progress_cb=cb,
                              cancel_event=self._cancel)
                exe = install_archive(src.kind, dest)
                self._dl_queue.put(("installed", str(exe)))
            except Exception as exc:
                self._dl_queue.put(("dl_error", f"{exc.__class__.__name__}: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def _cancel_download(self) -> None:
        self._cancel.set()
        self._prog_var.set("正在取消…（进度已保存，可续传）")

    def _open_browser(self) -> None:
        src = self._selected_source()
        if src and src.kind == "browser":
            webbrowser.open(src.url)
        else:
            webbrowser.open("https://www.opendesign.com/guestfiles/oda_file_converter")

    def _poll_dl(self) -> None:
        try:
            while True:
                msg = self._dl_queue.get_nowait()
                kind = msg[0]
                if kind == "speed":
                    self._test_btn.configure(state="normal")
                    self.tree.delete(*self.tree.get_children())
                    for s in self._sources:
                        size = f"{s.size_mb:.0f}MB" if s.size_mb else "—"
                        if s.kind == "browser":
                            speed = "浏览器安装"
                        elif s.reachable is False:
                            speed = "不可达 ✗"
                        elif s.speed_kbps:
                            speed = f"{s.speed_kbps:.0f}KB/s"
                        else:
                            speed = "未测速"
                        self.tree.insert("", "end", iid=s.id, values=(s.label, size, speed, s.note))
                    fastest = next((s for s in self._sources
                                    if s.kind != "browser" and s.reachable), None)
                    if fastest:
                        self.tree.selection_set(fastest.id)
                        self._prog_var.set(f"最快源已选中：{fastest.label[:26]}…（双击即可下载）")
                    else:
                        self._prog_var.set("所有直连源不可达——请检查网络，或用「打开官网」")
                elif kind == "prog":
                    _, done, total, speed, eta = msg
                    pct = 100 * done / max(total, 1)
                    self._prog.configure(value=pct)
                    eta_s = format_duration(eta) if eta else "…"
                    self._prog_var.set(
                        f"{pct:.0f}%  {done/1e6:.1f}/{total/1e6:.1f}MB  "
                        f"{speed/1024:.0f}KB/s  剩余 {eta_s}")
                elif kind == "installed":
                    self._downloading = False
                    self._dl_btn.configure(state="normal")
                    self._cancel_btn.configure(state="disabled")
                    self._prog.configure(value=100)
                    self._prog_var.set("下载并安装完成 ✓")
                    self._status_var.set(f"已安装：{msg[1]}")
                    if self._on_finished:
                        self._on_finished()
                    self._messagebox.showinfo(
                        "引擎助手", "安装完成！现在可以直接转换 DWG 文件了。", parent=self.win)
                elif kind == "dl_error":
                    self._downloading = False
                    self._dl_btn.configure(state="normal")
                    self._cancel_btn.configure(state="disabled")
                    self._prog_var.set(f"下载失败：{msg[1][:60]}")
                    if "取消" not in msg[1]:
                        self._messagebox.showerror("下载失败", msg[1], parent=self.win)
        except queue.Empty:
            pass
        try:
            if self.win.winfo_exists():
                self.win.after(150, self._poll_dl)
        except Exception:
            pass


def main() -> int:
    """GUI entry point (dwg2pdf-gui / dwg2pdf --gui). Returns exit code."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError as exc:
        print(f"GUI unavailable: tkinter is missing from this Python ({exc}).", file=sys.stderr)
        print("Linux: install python3-tk (apt) or use a python.org/uv build. "
              "The CLI works regardless: dwg2pdf --help", file=sys.stderr)
        return 2

    multiprocessing.freeze_support()
    try:
        app = App(tk, ttk, filedialog, messagebox, ScrolledText)
    except tk.TclError as exc:
        print(f"无法打开图形界面（{exc}）；请使用命令行模式：dwg2pdf --help", file=sys.stderr)
        return 2
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
