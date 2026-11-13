"""创建 GitHub Release 并上传构建产物。

用法:
  python make_release.py <owner/repo> <tag> <文件...>
"""
import json
import os
import sys
import urllib.request
import urllib.error
import mimetypes

TOKEN = os.environ.get("GH_TOKEN", "")
API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"

NOTES = """## dwg2pdf 1.0.0 — Go 重写版

把 DWG/DXF 批量转成矢量 PDF 的高性能工具，**单文件零依赖**。

### 性能

| 图纸 | 原 Python 版 | 本版本 |
|---|---|---|
| 38.5 MB DXF（1.5 万实体 / 2447 块） | 1465 秒 | **1.0 秒** |
| 5 MB DWG（含外部转换） | 1465 秒 | **5.2 秒** |

约 **800 倍**提速。

### 亮点

- **文字可搜索**：内嵌 Type0/CID 字体 + ToUnicode，PDF 里的中文能选中复制（原版全部转成矢量线条，一个字都搜不到）
- **零依赖**：内置思源黑体子集（3.6 MB），不需要 Python、字体或任何运行库
- **可替换字库**：`--font-dir` 指向自己的 TTF/OTF 目录即可换字体，自动映射 SHX 字体名
- **DWG 自动回退**：内置 LibreDWG 失败时自动切换到 ODA File Converter
- **跨平台**：Windows / macOS / Linux 全支持

### 用法

```bash
dwg2pdf drawing.dwg                    # 单文件
dwg2pdf ./drawings -r -o ./pdf         # 递归批量
dwg2pdf *.dwg --monochrome --paper a3  # 黑白 A3
dwg2pdf --check                        # 环境自检
```

### 说明

- Windows 包未做代码签名，首次运行 SmartScreen 提示时选「仍要运行」
- DWG 输入需要 ODA File Converter（免费）或内置 LibreDWG；DXF 开箱即用
- 本版本位于 `v1-go` 分支，原 Python 实现仍在 `main`
"""


def req(method, url, payload=None, raw=None, content_type=None):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dwg2pdf-release",
    }
    data = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = content_type or "application/octet-stream"
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:900]
        raise RuntimeError(f"{method} {url} -> {e.code}\n{detail}")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    repo, tag = sys.argv[1], sys.argv[2]
    files = sys.argv[3:]

    if not TOKEN:
        print("错误: 未设置 GH_TOKEN", file=sys.stderr)
        return 1

    # 1. 创建或复用 release
    try:
        rel = req("POST", f"{API}/repos/{repo}/releases", {
            "tag_name": tag,
            "target_commitish": "v1-go",
            "name": f"dwg2pdf {tag} (Go 重写版)",
            "body": NOTES,
            "draft": False,
            "prerelease": False,
        })
        print(f"已创建 release {tag}")
    except RuntimeError as e:
        if "422" in str(e) or "already_exists" in str(e):
            rel = req("GET", f"{API}/repos/{repo}/releases/tags/{tag}")
            print(f"复用已有 release {tag}")
        else:
            raise

    upload_url = rel["upload_url"].split("{")[0]

    # 2. 上传资产
    for f in files:
        name = os.path.basename(f)
        size = os.path.getsize(f)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        with open(f, "rb") as fh:
            data = fh.read()
        print(f"上传 {name} ({size/1024/1024:.2f} MB)...")
        try:
            req("POST", f"{upload_url}?name={name}", raw=data, content_type=ctype)
            print("  完成")
        except RuntimeError as e:
            if "already_exists" in str(e):
                print("  已存在，跳过")
            else:
                raise

    print(f"\n完成: {rel['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
