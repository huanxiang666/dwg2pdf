"""通过 GitHub Git Data API 把本地目录发布为一个新分支。

用法:
  python publish.py <本地目录> <owner/repo> <新分支名> [基准分支]

不依赖 git 命令行，直接构造 tree + commit + ref。
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

TOKEN = os.environ.get("GH_TOKEN", "")
API = "https://api.github.com"

# 需要跳过的路径
SKIP_DIRS = {".git", "dist", "pkg", "test-out", "__pycache__", ".venv"}
SKIP_EXT = {".exe", ".zip", ".tar.gz", ".png", ".log"}


def req(method, path, payload=None):
    url = API + path if path.startswith("/") else path
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dwg2pdf-publisher",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"{method} {url} -> {e.code}\n{detail}")


def collect(local):
    """返回 [(相对路径, 绝对路径)]，路径统一用 / 分隔。"""
    out = []
    for root, dirs, files in os.walk(local):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if any(fn.endswith(ext) for ext in SKIP_EXT):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, local).replace("\\", "/")
            out.append((rel, full))
    return sorted(out)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    local, repo, branch = sys.argv[1], sys.argv[2], sys.argv[3]
    base = sys.argv[4] if len(sys.argv) > 4 else "main"

    if not TOKEN:
        print("错误: 未设置 GH_TOKEN", file=sys.stderr)
        return 1

    files = collect(local)
    print(f"待上传文件: {len(files)} 个")
    total = sum(os.path.getsize(f) for _, f in files)
    print(f"总大小: {total/1024/1024:.2f} MB")

    # 1. 基准 commit
    ref = req("GET", f"/repos/{repo}/git/ref/heads/{base}")
    base_sha = ref["object"]["sha"]
    base_commit = req("GET", f"/repos/{repo}/git/commits/{base_sha}")
    base_tree = base_commit["tree"]["sha"]
    print(f"基准分支 {base}: {base_sha[:8]}")

    # 2. 创建 blob
    entries = []
    for i, (rel, full) in enumerate(files, 1):
        with open(full, "rb") as f:
            raw = f.read()
        blob = req("POST", f"/repos/{repo}/git/blobs", {
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({
            "path": rel,
            "mode": "100755" if rel.endswith(".sh") else "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        if i % 10 == 0 or i == len(files):
            print(f"  已上传 {i}/{len(files)}")

    # 3. 创建 tree
    # 用 base_tree=None（而非 base_tree）来生成"只含本次提交文件"的树，
    # 避免把基准分支的旧文件（如原 Python 实现）一并带进来。
    tree = req("POST", f"/repos/{repo}/git/trees", {
        "tree": entries,
    })
    print(f"tree: {tree['sha'][:8]}")

    # 4. 创建 commit
    msg = "feat: Go 重写 dwg2pdf（性能提升约 800 倍）\n\n" \
          "- 自研 DXF 解析器（38.5MB / 190ms）\n" \
          "- 零依赖 PDF 写入器，内嵌 Type0/CID 字体 + ToUnicode，文字可搜索\n" \
          "- 多线程渲染，40MB 图纸 1 秒完成（原 Python 版 1465 秒）\n" \
          "- 可替换字库系统（内嵌 Noto Sans SC 子集 3.6MB）\n" \
          "- 支持 Windows/macOS/Linux，零外部依赖\n" \
          "- LibreDWG 内置 + ODA 自动回退\n"
    commit = req("POST", f"/repos/{repo}/git/commits", {
        "message": msg,
        "tree": tree["sha"],
        "parents": [base_sha],
    })
    print(f"commit: {commit['sha'][:8]}")

    # 5. 创建/更新分支 ref
    try:
        req("POST", f"/repos/{repo}/git/refs", {
            "ref": f"refs/heads/{branch}",
            "sha": commit["sha"],
        })
        print(f"已创建分支 {branch}")
    except RuntimeError as e:
        if "422" in str(e) or "already exists" in str(e):
            req("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {
                "sha": commit["sha"],
                "force": False,
            })
            print(f"已更新分支 {branch}")
        else:
            raise

    print(f"\n完成: https://github.com/{repo}/tree/{branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
