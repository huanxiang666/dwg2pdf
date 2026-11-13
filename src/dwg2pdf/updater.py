"""Manual update check against GitHub Releases.

Design notes:
- User-triggered only (menu/toolbar button); the app never phones home on
  startup or on a timer.
- This repository hosts TWO release trains (the Python GUI application and a
  separate Go CLI rewrite published under v1.x tags). To avoid telling GUI
  users that a CLI-only build is "their" update, releases are matched by the
  GUI-train asset naming pattern (windows setup.exe / linux .deb / *-linux-x64
  archives). A release without matching assets is ignored.
- Network failures degrade to a friendly message; nothing raises to the UI.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field

REPO = "huanxiang666/dwg2pdf"
API_RELEASES = f"https://api.github.com/repos/{REPO}/releases"
GUI_ASSET_RE = re.compile(
    r"^dwg2pdf[_-]v?\d+\.\d+\.\d+.*(-windows-x64-setup\.exe|_amd64\.deb|-linux-x64\.tar\.gz)$"
)
USER_AGENT = "dwg2pdf-update-check"


@dataclass
class UpdateInfo:
    current: str
    latest: str | None = None
    url: str | None = None
    notes: str = ""
    assets: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return bool(self.latest) and _semver(self.latest) > _semver(self.current)


def _semver(tag: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
    if not m:
        return (0, 0, 0)
    return tuple(int(g) for g in m.groups())


def _http_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def check_for_updates(current_version: str) -> UpdateInfo:
    """Fetch the newest GUI-train release and compare versions.

    Never raises: network/parse problems come back as ``UpdateInfo.error``.
    """
    info = UpdateInfo(current=current_version)
    try:
        releases = _http_json(f"{API_RELEASES}?per_page=15")
    except Exception as exc:
        info.error = f"无法连接 GitHub（{exc.__class__.__name__}）；请检查网络后重试"
        return info
    if not isinstance(releases, list):
        info.error = "GitHub 返回了非预期数据"
        return info

    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        names = [a.get("name", "") for a in rel.get("assets", [])]
        gui_assets = [n for n in names if GUI_ASSET_RE.match(n)]
        if not gui_assets:
            continue  # other release train (e.g. the Go CLI builds)
        tag = str(rel.get("tag_name", "")).lstrip("v")
        info.latest = tag
        info.url = rel.get("html_url")
        body = (rel.get("body") or "").strip()
        info.notes = body[:600]
        info.assets = gui_assets
        break
    if info.latest is None:
        info.error = "未找到与本程序匹配的发布版本"
    return info
