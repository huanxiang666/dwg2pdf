"""Tkinter GUI for dwg2pdf (entry point: dwg2pdf-gui / dwg2pdf --gui).

All heavy work runs in a background thread; the Tk main loop only receives
queue messages, which keeps the UI responsive and thread-safe.
"""

from __future__ import annotations

import io
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__
from .batch import collect_inputs, plan_targets, run_batch
from .dwg_backend import detect_converter
from .options import PAPER_CHOICES, ConvertOptions, OptionError

_LAYOUT_CHOICES = ["auto — 图纸布局优先", "modelspace — 模型空间", "all — 全部布局"]
_LAYOUT_VALUES = ["auto", "modelspace", "all"]
_PAPER_CHOICES_CN = ["auto — 自动/图纸原尺寸"] + list(PAPER_CHOICES[1:])
_POLL_MS = 100


def _open_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    """Main window. Built with plain tk/ttk so it works headless-free anywhere."""

    def __init__(self, tk, ttk, filedialog, messagebox, ScrolledText):
        self._tk_mods = (tk, ttk, filedialog, messagebox, ScrolledText)
        self.files: list[str] = []
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._running = False
        self._last_output_dir: str | None = None

        tk_, ttk_ = tk, ttk
        self.root = tk_.Tk()
        self.root.title(f"dwg2pdf {__version__} — DWG/DXF 批量转 PDF")
        self.root.geometry("880x640")
        self.root.minsize(760, 560)

        # --- file list -----------------------------------------------------
        top = ttk_.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="both", expand=True)
        list_frame = ttk_.Labelframe(top, text="待转换文件 (.dwg / .dxf)")
        list_frame.pack(side="left", fill="both", expand=True)
        self.listbox = tk_.Listbox(list_frame, selectmode="extended", activestyle="none")
        sb = ttk_.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb.pack(side="right", fill="y", pady=6)

        btns = ttk_.Frame(top)
        btns.pack(side="right", fill="y", padx=(8, 0))
        ttk_.Button(btns, text="添加文件…", command=self.add_files).pack(fill="x", pady=2)
        ttk_.Button(btns, text="添加文件夹…", command=self.add_folder).pack(fill="x", pady=2)
        ttk_.Button(btns, text="移除选中", command=self.remove_selected).pack(fill="x", pady=2)
        ttk_.Button(btns, text="清空列表", command=self.clear_files).pack(fill="x", pady=2)
        self.recursive_var = tk_.BooleanVar(value=True)
        ttk_.Checkbutton(btns, text="文件夹递归扫描", variable=self.recursive_var).pack(fill="x", pady=(8, 2))

        # --- options -------------------------------------------------------
        opts = ttk_.Labelframe(self.root, text="转换选项", padding=8)
        opts.pack(fill="x", padx=10, pady=4)

        row1 = ttk_.Frame(opts)
        row1.pack(fill="x")
        ttk_.Label(row1, text="布局:").pack(side="left")
        self.layout_var = tk_.StringVar(value=_LAYOUT_CHOICES[0])
        ttk_.Combobox(row1, textvariable=self.layout_var, values=_LAYOUT_CHOICES,
                      state="readonly", width=22).pack(side="left", padx=(4, 16))
        ttk_.Label(row1, text="纸张:").pack(side="left")
        self.paper_var = tk_.StringVar(value=_PAPER_CHOICES_CN[0])
        ttk_.Combobox(row1, textvariable=self.paper_var, values=_PAPER_CHOICES_CN,
                      state="readonly", width=22).pack(side="left", padx=(4, 16))
        self.mono_var = tk_.BooleanVar(value=False)
        ttk_.Checkbutton(row1, text="单色输出（黑白打印）", variable=self.mono_var).pack(side="left")

        row2 = ttk_.Frame(opts)
        row2.pack(fill="x", pady=(6, 0))
        ttk_.Label(row2, text="输出到:").pack(side="left")
        self.output_mode_var = tk_.StringVar(value="source")
        ttk_.Radiobutton(row2, text="源文件同目录", variable=self.output_mode_var,
                         value="source").pack(side="left", padx=(4, 8))
        ttk_.Radiobutton(row2, text="指定目录:", variable=self.output_mode_var,
                         value="custom").pack(side="left")
        self.output_dir_var = tk_.StringVar(value="")
        ttk_.Entry(row2, textvariable=self.output_dir_var, width=36).pack(side="left", padx=4, fill="x", expand=True)
        ttk_.Button(row2, text="浏览…", command=self.choose_output_dir).pack(side="left")
        ttk_.Label(row2, text="并行:").pack(side="left", padx=(12, 2))
        self.jobs_var = tk_.IntVar(value=0)
        ttk_.Spinbox(row2, from_=0, to=max(16, (os.cpu_count() or 4) * 2),
                     textvariable=self.jobs_var, width=4).pack(side="left")
        self.skip_var = tk_.BooleanVar(value=False)
        ttk_.Checkbutton(row2, text="跳过已存在的 PDF", variable=self.skip_var).pack(side="left", padx=(12, 0))

        # --- progress + log --------------------------------------------------
        prog_frame = ttk_.Frame(self.root, padding=(10, 4))
        prog_frame.pack(fill="x")
        self.progress = ttk_.Progressbar(prog_frame, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.status_var = tk_.StringVar(value="就绪")
        ttk_.Label(prog_frame, textvariable=self.status_var, width=24, anchor="e").pack(side="right", padx=(8, 0))

        self.log = ScrolledText(self.root, height=9, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=False, padx=10, pady=4)

        bottom = ttk_.Frame(self.root, padding=(10, 4, 10, 10))
        bottom.pack(fill="x")
        self.backend_var = tk_.StringVar(value="检测 DWG 转换器…")
        ttk_.Label(bottom, textvariable=self.backend_var, foreground="#555").pack(side="left")
        ttk_.Button(bottom, text="环境检查", command=self.show_check).pack(side="right")
        ttk_.Button(bottom, text="打开输出目录", command=self.open_output).pack(side="right", padx=6)
        self.convert_btn = ttk_.Button(bottom, text="开始转换", command=self.start_convert)
        self.convert_btn.pack(side="right", padx=6)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(_POLL_MS, self._poll_queue)
        threading.Thread(target=self._detect_backend_async, daemon=True).start()

    # ------------------------------------------------------------------ files
    def _refresh_listbox(self) -> None:
        tk_ = self._tk_mods[0]
        self.listbox.delete(0, tk_.END)
        for f in self.files:
            self.listbox.insert(tk_.END, f)
        self.status_var.set(f"{len(self.files)} 个文件")

    def add_files(self) -> None:
        filedialog = self._tk_mods[2]
        paths = filedialog.askopenfilenames(
            title="选择 CAD 图纸",
            filetypes=[("CAD 图纸", "*.dwg *.dxf *.DWG *.DXF"), ("所有文件", "*.*")],
        )
        self._add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        filedialog = self._tk_mods[2]
        folder = filedialog.askdirectory(title="选择包含图纸的文件夹")
        if not folder:
            return
        files, ignored = collect_inputs([folder], recursive=self.recursive_var.get())
        for path in ignored:
            self._log(f"提示: {path} 中未找到 CAD 文件")
        self._add_paths(files)

    def _add_paths(self, paths: list[Path]) -> None:
        existing = set(self.files)
        added = 0
        for p in paths:
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
        filedialog = self._tk_mods[2]
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.output_dir_var.set(folder)
            self.output_mode_var.set("custom")

    # ---------------------------------------------------------------- convert
    def _build_opts(self) -> ConvertOptions:
        layout_idx = _LAYOUT_CHOICES.index(self.layout_var.get())
        layout = _LAYOUT_VALUES[layout_idx]
        paper = self.paper_var.get().split(" — ")[0].strip()
        output_dir = None
        if self.output_mode_var.get() == "custom":
            output_dir = self.output_dir_var.get().strip()
            if not output_dir:
                raise OptionError("已选择“指定目录”，但未填写目录")
        return ConvertOptions(
            layout=layout,
            monochrome=self.mono_var.get(),
            paper=paper,
            jobs=self.jobs_var.get(),
            output_dir=output_dir,
            skip_existing=self.skip_var.get(),
        ).validate()

    def start_convert(self) -> None:
        if self._running:
            return
        if not self.files:
            self._tk_mods[3].showwarning("dwg2pdf", "请先添加 .dwg / .dxf 文件")
            return
        try:
            opts = self._build_opts()  # read widgets on the UI thread only
        except OptionError as exc:
            self._tk_mods[3].showerror("dwg2pdf", str(exc))
            return
        files = [Path(f) for f in self.files]
        if opts.output_dir:
            Path(opts.output_dir).expanduser().mkdir(parents=True, exist_ok=True)
        self._last_output_dir = opts.output_dir or str(files[0].parent)
        plans = plan_targets(files, opts.output_dir)
        self._running = True
        self.convert_btn.configure(state="disabled")
        self.progress.configure(maximum=len(plans), value=0)
        self.status_var.set(f"0 / {len(plans)}")
        self._log(f"开始转换 {len(plans)} 个文件 → {opts.output_dir or '源文件目录'}")
        threading.Thread(target=self._convert_worker, args=(plans, opts), daemon=True).start()

    def _convert_worker(self, plans, opts: ConvertOptions) -> None:
        try:
            def progress_cb(result, done: int, total: int) -> None:
                self.q.put(("progress", result, done, total))

            batch = run_batch(plans, opts, progress_cb=progress_cb)
            self.q.put(("done", batch))
        except Exception as exc:  # surface any unexpected crash to the UI
            self.q.put(("error", f"{exc.__class__.__name__}: {exc}"))

    # ------------------------------------------------------------------- misc
    def open_output(self) -> None:
        target = self._last_output_dir
        if not target and self.output_mode_var.get() == "custom":
            target = self.output_dir_var.get().strip()
        if not target and self.files:
            target = str(Path(self.files[0]).parent)
        if not target or not Path(target).exists():
            self._tk_mods[3].showinfo("dwg2pdf", "还没有输出目录")
            return
        try:
            _open_in_file_manager(Path(target))
        except Exception as exc:
            self._tk_mods[3].showerror("dwg2pdf", f"无法打开目录: {exc}")

    def show_check(self) -> None:
        from .cli import run_check

        buf = io.StringIO()
        old = sys.stdout
        try:
            sys.stdout = buf
            run_check()
        finally:
            sys.stdout = old
        tk_ = self._tk_mods[0]
        win = tk_.Toplevel(self.root)
        win.title("环境检查")
        win.geometry("640x420")
        text = tk_.Text(win, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", buf.getvalue())
        text.configure(state="disabled")

    def _detect_backend_async(self) -> None:
        converter = detect_converter()
        if converter:
            self.q.put(("backend", f"DWG 转换器: {converter.display_name}"))
        else:
            self.q.put(("backend", "未检测到 DWG 转换器 — DWG 需安装 ODA File Converter 或 LibreDWG（见“环境检查”）；DXF 可直接转换"))

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, result, done, total = msg
                    self.progress.configure(value=done)
                    self.status_var.set(f"{done} / {total}")
                    if result.status == "ok":
                        self._log(f"✔ {Path(result.source).name} → {Path(result.target).name} ({result.pages} 页, {result.seconds:.1f}s)")
                        for warning in result.warnings:
                            self._log(f"  ⚠ {Path(result.source).name}: {warning}")
                    elif result.status == "skipped":
                        self._log(f"⊘ {Path(result.source).name} 已存在，跳过")
                    else:
                        first = result.error.splitlines()[0] if result.error else "未知错误"
                        self._log(f"✘ {Path(result.source).name}: {first}")
                elif kind == "done":
                    batch = msg[1]
                    self._running = False
                    self.convert_btn.configure(state="normal")
                    summary = f"完成: {len(batch.ok)} 成功, {len(batch.failed)} 失败, {len(batch.skipped)} 跳过 ({batch.total_seconds:.1f}s)"
                    self.status_var.set(summary)
                    self._log(summary)
                    if batch.failed:
                        self._tk_mods[3].showwarning("dwg2pdf", summary + "\n失败详情见日志")
                    else:
                        self._tk_mods[3].showinfo("dwg2pdf", summary)
                elif kind == "error":
                    self._running = False
                    self.convert_btn.configure(state="normal")
                    self._log(f"转换出错: {msg[1]}")
                    self._tk_mods[3].showerror("dwg2pdf", msg[1])
                elif kind == "backend":
                    self.backend_var.set(msg[1])
        except queue.Empty:
            pass
        self.root.after(_POLL_MS, self._poll_queue)

    def _on_close(self) -> None:
        if self._running:
            if not self._tk_mods[3].askyesno("dwg2pdf", "正在转换中，确定要退出吗？"):
                return
            # A conversion is in flight on a daemon thread that owns a
            # ProcessPoolExecutor. Its worker processes + the executor's atexit
            # join would otherwise keep this (now windowless) process alive
            # until the whole batch finished. User asked to abort → hard exit.
            self.root.destroy()
            os._exit(0)
        self.root.destroy()


def main() -> int:
    multiprocessing.freeze_support()
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError as exc:
        print(f"GUI unavailable: tkinter is missing from this Python ({exc}).", file=sys.stderr)
        print("Linux: install python3-tk (apt) or use a python.org/uv build. "
              "The CLI works regardless: dwg2pdf --help", file=sys.stderr)
        return 2
    app = App(tk, ttk, filedialog, messagebox, ScrolledText)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
