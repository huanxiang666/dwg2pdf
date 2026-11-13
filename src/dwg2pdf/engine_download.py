"""Engine & fast-core downloader: multi-source, multi-threaded, resumable.

Beginner-friendly goals:
- One dialog lists every available source with a measured speed; the fastest
  working source is recommended. Sources are official endpoints only
  (GitHub release direct links and well-known GitHub mirror proxies) — no
  random third-party re-uploads (supply-chain safety).
- Downloads are chunked over HTTP Range with N threads, show percent/speed/
  ETA, and resume from ``.part`` files after interruption.
- After download the archive is unpacked into the per-user engine directory
  and ``dwg_backend.detect_converter()`` immediately picks it up.

What can be downloaded:
- ``libredwg-win64``  dwg2dxf.exe + DLLs from the OFFICIAL LibreDWG GitHub
  release (verified asset layout: exe 335 KB + libredwg-0.dll + libiconv +
  pcre2). Gives Windows users DWG support without ODA.
- ``go-core-*``       the optional Go fast-render core published in this
  repository's releases (DXF -> PDF, searchable text). Experimental.
- ``oda-browser``     not downloadable directly (ODA moved downloads behind
  an SSO login), so the assistant opens the official page in a browser and
  the app auto-detects ODA after the user installs it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

LIBREDWG_VERSION = "0.14.8958"
_LIBREDWG_ZIP = f"libredwg-{LIBREDWG_VERSION}-win64.zip"
_LIBREDWG_ORIGIN = (
    f"https://github.com/LibreDWG/libredwg/releases/download/{LIBREDWG_VERSION}/{_LIBREDWG_ZIP}"
)
GO_CORE_TAG = "v1.1.1"
_GO_ORIGIN = "https://github.com/huanxiang666/dwg2pdf/releases/download"
MIRROR_PREFIXES = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://ghfast.top/",
)

#: Files extracted from the LibreDWG win64 zip to make dwg2dxf.exe runnable.
LIBREDWG_WIN_FILES = (
    "dwg2dxf.exe",
    "libredwg-0.dll",
    "libiconv-2.dll",
    "libpcre2-8-0.dll",
    "libpcre2-16-0.dll",
)


@dataclass
class DownloadSource:
    id: str
    label: str
    url: str
    kind: str                     # libredwg-win64 | go-core | browser
    size_mb: float | None = None
    note: str = ""
    speed_kbps: float | None = None  # filled by speed_test()
    reachable: bool | None = None


def user_engine_dir() -> Path:
    """Per-user directory where downloaded engines/cores are installed."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "dwg2pdf" / "engine"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/dwg2pdf/engine"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "dwg2pdf" / "engine"


def available_sources() -> list[DownloadSource]:
    """Sources relevant for the running platform, best-first ordering."""
    sources: list[DownloadSource] = []
    machine = platform.machine().lower()

    if sys.platform == "win32":
        sources.append(DownloadSource(
            id="libredwg-official",
            label=f"LibreDWG {LIBREDWG_VERSION}（官方 GitHub，含 dwg2dxf.exe）",
            url=_LIBREDWG_ORIGIN,
            kind="libredwg-win64",
            size_mb=13.0,
            note="安装后 DWG 直接可转，无需其他软件",
        ))
        for i, prefix in enumerate(MIRROR_PREFIXES, 1):
            sources.append(DownloadSource(
                id=f"libredwg-mirror-{i}",
                label=f"LibreDWG {LIBREDWG_VERSION}（加速镜像 {i}：{prefix.split('//')[1].rstrip('/')}）",
                url=prefix + _LIBREDWG_ORIGIN,
                kind="libredwg-win64",
                size_mb=13.0,
                note="内容与官方源完全相同，国内网络通常更快",
            ))
        go_win_url = f"{_GO_ORIGIN}/{GO_CORE_TAG}/dwg2pdf-{GO_CORE_TAG}-windows-amd64.zip"
        sources.append(DownloadSource(
            id="go-core-win",
            label="Go 高速转换核心（实验性，DXF 秒级转换/文字可搜索）",
            url=go_win_url,
            kind="go-core",
            size_mb=12.2,
            note="可选增强；DWG 仍需上面的引擎",
        ))
        sources.append(DownloadSource(
            id="go-core-win-mirror",
            label="Go 高速转换核心（加速镜像）",
            url=MIRROR_PREFIXES[0] + go_win_url,
            kind="go-core",
            size_mb=12.2,
            note="内容与官方源相同",
        ))
        sources.append(DownloadSource(
            id="oda-browser",
            label="ODA File Converter（官网下载页，浏览器手动安装）",
            url="https://www.opendesign.com/guestfiles/oda_file_converter",
            kind="browser",
            note="官网需登录邮箱获取；装好后本软件自动识别",
        ))
    elif sys.platform == "darwin":
        sources.append(DownloadSource(
            id="go-core-mac-arm" if machine == "arm64" else "go-core-mac-amd",
            label="Go 高速转换核心（实验性）",
            url=f"{_GO_ORIGIN}/{GO_CORE_TAG}/dwg2pdf-{GO_CORE_TAG}-darwin-{'arm64' if machine == 'arm64' else 'amd64'}.tar.gz",
            kind="go-core",
            size_mb=4.0,
            note="可选增强；DWG 引擎请用 brew install libredwg 或安装 ODA",
        ))
        sources.append(DownloadSource(
            id="oda-browser",
            label="ODA File Converter（官网下载页，浏览器手动安装）",
            url="https://www.opendesign.com/guestfiles/oda_file_converter",
            kind="browser",
            note="装好后本软件自动识别",
        ))
    else:  # linux and friends
        # The Linux release bundle already ships dwg2dxf; offer the fast core
        # and, for pip/source installs without a bundled engine, the same
        # official tarball users can build (advanced).
        go_linux_url = f"{_GO_ORIGIN}/{GO_CORE_TAG}/dwg2pdf-{GO_CORE_TAG}-linux-amd64.tar.gz"
        sources.append(DownloadSource(
            id="go-core-linux",
            label="Go 高速转换核心（实验性，DXF 秒级转换/文字可搜索）",
            url=go_linux_url,
            kind="go-core",
            size_mb=4.1,
            note="可选增强；DWG 仍需 dwg2dxf 引擎",
        ))
        sources.append(DownloadSource(
            id="go-core-linux-mirror",
            label="Go 高速转换核心（加速镜像）",
            url=MIRROR_PREFIXES[0] + go_linux_url,
            kind="go-core",
            size_mb=4.1,
            note="内容与官方源相同",
        ))
        sources.append(DownloadSource(
            id="libredwg-src",
            label=f"LibreDWG {LIBREDWG_VERSION} 源码包（Linux 高级用户，需自行编译）",
            url=f"https://github.com/LibreDWG/libredwg/releases/download/{LIBREDWG_VERSION}/libredwg-{LIBREDWG_VERSION}.tar.xz",
            kind="browser",
            size_mb=6.0,
            note="推荐直接用 .deb 安装包：已内置编译好的引擎",
        ))
    return sources


# --------------------------------------------------------------------- speed
def _probe_range(url: str, nbytes: int = 256 * 1024, timeout: float = 10.0):
    """GET the first ``nbytes``; returns (bytes_downloaded, seconds) or None."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "dwg2pdf-engine-downloader",
        "Range": f"bytes=0-{nbytes - 1}",
    })
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(nbytes)
        dt = time.monotonic() - t0
        if len(data) == 0 or dt <= 0:
            return None
        return len(data), dt
    except Exception:
        return None


def speed_test(sources: list[DownloadSource], timeout: float = 10.0) -> list[DownloadSource]:
    """Probe every downloadable source in parallel; fill speed/reachable.

    'browser' sources are not probed (they are pages, not files).
    Returns the same list sorted fastest-first (unreachable last).
    """
    targets = [s for s in sources if s.kind != "browser"]

    def run(src: DownloadSource) -> None:
        got = _probe_range(src.url, timeout=timeout)
        if got is None:
            src.reachable = False
            src.speed_kbps = None
        else:
            nbytes, dt = got
            src.reachable = True
            src.speed_kbps = round(nbytes / dt / 1024.0, 1)

    threads = [threading.Thread(target=run, args=(s,), daemon=True) for s in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 5)

    def sort_key(s: DownloadSource):
        if s.kind == "browser":
            return (2, 0.0)
        if not s.reachable:
            return (1, 0.0)
        return (0, -(s.speed_kbps or 0.0))

    return sorted(sources, key=sort_key)


# ------------------------------------------------------------------ download
@dataclass
class DownloadProgress:
    done_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    error: str | None = None
    finished: bool = False


class _ChunkWorker(threading.Thread):
    """Downloads [start, end] of a URL into its own handle at the right offset.

    One handle per worker avoids cross-thread seek/write races; each worker
    only touches its own non-overlapping byte range. ``have`` bytes are
    already on disk (resume), so the request starts at ``start + have``.
    """

    def __init__(self, url: str, path: Path, start: int, end: int, have: int,
                 state: dict, idx: int, cancel_event: threading.Event | None):
        super().__init__(daemon=True)
        self.url, self.path = url, path
        # NOTE: never name these self.start/self.end — that would shadow
        # threading.Thread.start() and make w.start() call an int.
        self.range_start, self.range_end = start, end
        self.have = have
        self.state, self.idx = state, idx
        self.cancel_event = cancel_event
        self.error: str | None = None
        self.downloaded = have  # bytes of this chunk now on disk

    def run(self) -> None:
        pos = self.range_start + self.have
        if pos > self.range_end:
            return
        req = urllib.request.Request(self.url, headers={
            "User-Agent": "dwg2pdf-engine-downloader",
            "Range": f"bytes={pos}-{self.range_end}",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, \
                    open(self.path, "r+b") as fh:
                fh.seek(pos)
                while True:
                    if self.cancel_event is not None and self.cancel_event.is_set():
                        self.error = "cancelled"
                        break
                    buf = resp.read(256 * 1024)
                    if not buf:
                        break
                    fh.write(buf)
                    self.downloaded += len(buf)
                    with self.state["lock"]:
                        self.state["done"] += len(buf)
        except Exception as exc:
            if self.error != "cancelled":
                self.error = f"{exc.__class__.__name__}: {exc}"


def _remote_size(url: str, timeout: float = 15.0) -> tuple[int | None, bool]:
    """(content_length, supports_range) via a 1-byte Range probe."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "dwg2pdf-engine-downloader", "Range": "bytes=0-0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            cr = resp.headers.get("Content-Range")  # bytes 0-0/13347437
            if status == 206 and cr and "/" in cr:
                return int(cr.rsplit("/", 1)[1]), True
            cl = resp.headers.get("Content-Length")
            return (int(cl) if cl else None), False
    except Exception:
        return None, False


def download_file(
    url: str,
    dest: Path,
    threads: int = 4,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Multi-threaded Range download with resume; returns dest on success.

    ``progress_cb(DownloadProgress)`` is called ~4x/second from a monitor
    thread. Raises RuntimeError with a human-readable message on failure;
    on cancel/interrupt the ``.part`` file and per-chunk meta are kept so a
    retry resumes where it stopped.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    meta_path = dest.with_name(dest.name + ".part.meta.json")

    total, supports_range = _remote_size(url)
    if total is None:
        raise RuntimeError("无法获取文件大小（网络不通或源已失效）")

    meta: dict = {"url": url, "total": total, "chunks": {}}
    if meta_path.exists() and part.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            if old.get("url") == url and old.get("total") == total:
                meta = old  # resume from saved chunk offsets
        except Exception:
            pass

    if supports_range:
        n_chunks = max(1, min(int(threads), int(total // (2 * 1024 * 1024)) or 1))
    else:
        n_chunks = 1
    bounds: list[tuple[int, int, int]] = []
    size = total // n_chunks
    for i in range(n_chunks):
        s = i * size
        e = total - 1 if i == n_chunks - 1 else (i + 1) * size - 1
        have = int(meta["chunks"].get(str(i), 0))
        have = max(0, min(have, e - s + 1))
        bounds.append((s, e, have))

    # Pre-allocate the part file so chunk writes can seek freely.
    with open(part, "a+b") as fh:
        fh.truncate(total)

    state = {"done": sum(h for _, _, h in bounds), "lock": threading.Lock()}
    workers = [
        _ChunkWorker(url, part, s, e, have, state, i, cancel_event)
        for i, (s, e, have) in enumerate(bounds)
        if have < (e - s + 1)
    ]
    prog = DownloadProgress(total_bytes=total, done_bytes=state["done"])
    stop = threading.Event()

    def monitor() -> None:
        t_prev, b_prev = time.monotonic(), state["done"]
        while not stop.is_set():
            time.sleep(0.25)
            now = time.monotonic()
            with state["lock"]:
                done = state["done"]
            dt = now - t_prev
            speed = (done - b_prev) / dt if dt > 0 else 0.0
            t_prev, b_prev = now, done
            prog.done_bytes = done
            prog.speed_bps = speed
            prog.eta_seconds = (total - done) / speed if speed > 1024 else None
            if progress_cb:
                progress_cb(prog)
            if done >= total:
                break

    mon = threading.Thread(target=monitor, daemon=True)
    mon.start()
    for w in workers:
        w.start()
    while any(w.is_alive() for w in workers):
        time.sleep(0.2)
    stop.set()
    mon.join(timeout=2)

    cancelled = cancel_event is not None and cancel_event.is_set()
    errors = [w.error for w in workers if w.error and w.error != "cancelled"]
    with state["lock"]:
        done = state["done"]

    if cancelled or errors or done < total:
        # Persist per-chunk progress from each worker's own counter (exact).
        meta["chunks"] = {str(w.idx): w.downloaded for w in workers}
        # Chunks that were already complete aren't in `workers`; keep old meta.
        for i, (s, e, have) in enumerate(bounds):
            if have >= (e - s + 1):
                meta["chunks"].setdefault(str(i), have)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        if cancelled:
            raise RuntimeError("下载已取消（进度已保存，可续传）")
        if errors:
            raise RuntimeError(f"下载中断：{errors[0]}（进度已保存，可续传）")
        raise RuntimeError(f"下载不完整（{done}/{total} 字节）")

    os.replace(part, dest)
    if meta_path.exists():
        meta_path.unlink()
    prog.finished = True
    prog.done_bytes = total
    if progress_cb:
        progress_cb(prog)
    return dest


# -------------------------------------------------------------------- install
def install_archive(kind: str, archive: Path) -> Path:
    """Unpack a downloaded archive into the user engine dir; return exe path."""
    engine_dir = user_engine_dir()
    engine_dir.mkdir(parents=True, exist_ok=True)

    if kind == "libredwg-win64":
        target = engine_dir / "libredwg-win64"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            missing = []
            for want in LIBREDWG_WIN_FILES:
                # zip layout may nest files in a folder; match by basename
                match = next((real for low, real in names.items()
                              if Path(low).name == want.lower()), None)
                if match is None:
                    missing.append(want)
                    continue
                with zf.open(match) as src_f, open(target / want, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                os.chmod(target / want, 0o755)
        if missing:
            raise RuntimeError(f"压缩包缺少文件: {', '.join(missing)}")
        return target / "dwg2dxf.exe"

    if kind == "go-core":
        target = engine_dir / "go-core"
        target.mkdir(parents=True, exist_ok=True)
        exe_name = "dwg2pdf-go.exe" if sys.platform == "win32" else "dwg2pdf-go"
        if archive.name.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                member = next(n for n in zf.namelist()
                              if Path(n).name.lower().startswith("dwg2pdf")
                              and not n.lower().endswith((".md", ".txt", "")))
                data = zf.read(member)
        else:  # tar.gz
            import tarfile

            with tarfile.open(archive, "r:gz") as tf:
                member = next(m for m in tf.getmembers()
                              if m.isfile() and Path(m.name).name == "dwg2pdf")
                data = tf.extractfile(member).read()
        exe = target / exe_name
        exe.write_bytes(data)
        os.chmod(exe, 0o755)
        return exe

    raise RuntimeError(f"未知安装类型: {kind}")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
