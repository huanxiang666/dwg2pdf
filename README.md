# dwg2pdf

把 CAD 图纸（**DWG / DXF**）批量转换成**矢量 PDF**的高性能工具，单文件零依赖，支持 Windows / macOS / Linux。

> Go 重写版。相比原 Python 实现，40 MB 级施工图从 **1465 秒降到 1~5 秒**（约 300~1000 倍），
> 并且**输出的文字可搜索、可复制**（原版全部转成了矢量线条）。

## 特性

- **快**：38.5 MB DXF 解析 190 ms，整个转换 1 秒内完成；4 核并行渲染
- **零依赖**：单文件可执行，内置思源黑体子集（3.6 MB），不需要装 Python、字体或任何运行库
- **文字可搜索**：内嵌 Type0/CID 字体 + ToUnicode，PDF 里的中文能选中复制
- **可替换字库**：`--font-dir` 指向自己的 TTF/OTF 目录即可换字体；自动映射 SHX 字体名
- **尊重 CAD 打印语义**：图纸空间按图纸自带纸张与比例，模型空间自动适配，支持 A0–A4 / ANSI / ARCH
- **真实语料验证**：用 5 MB 天正建筑施工图（18 张图纸、1.5 万实体、2447 个块）实测通过

## 安装

### 方式一：下载可执行包（推荐，无需任何依赖）

到 [Releases](https://github.com/huanxiang666/dwg2pdf/releases) 下载对应平台：

| 平台 | 文件 |
|---|---|
| Windows x64 | `dwg2pdf-x.y.z-windows-x64.zip` |
| Linux x64 | `dwg2pdf-x.y.z-linux-x64.tar.gz` |
| macOS (Intel) | `dwg2pdf-x.y.z-darwin-amd64.tar.gz` |
| macOS (Apple Silicon) | `dwg2pdf-x.y.z-darwin-arm64.tar.gz` |

解压后直接运行，无需安装。

### 方式二：从源码构建

```bash
go install github.com/huanxiang666/dwg2pdf/cmd/dwg2pdf@latest
```

## 用法

```bash
# 单文件（PDF 生成在源文件旁）
dwg2pdf drawing.dwg

# 整个目录递归转换
dwg2pdf ./drawings -r -o ./pdf

# 黑白 A3，8 进程并行
dwg2pdf *.dwg --monochrome --paper a3 -j 8

# 环境自检
dwg2pdf --check

# 查看可用字体
dwg2pdf --list-fonts
```

### 参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `-o` | 输出目录 | 源文件旁 |
| `-r` | 递归扫描目录 | 关 |
| `--paper` | `auto` / `a0`–`a4` / `letter` / `legal` | `auto` |
| `--monochrome` | 黑白输出 | 关 |
| `--scale` | 内容缩放系数 | `1` |
| `--margin` | 页边距（mm） | auto 纸张 0，指定纸张 5 |
| `-j` | 并行数（0 = CPU 核数） | `0` |
| `--line-width` | 基础线宽（mm） | `0.13` |
| `--skip-existing` | 跳过已存在的 PDF | 关 |
| `--font-dir` | 追加字体目录（可重复） | — |
| `--report` | 输出 JSON 批量报告 | — |
| `--check` | 环境自检 | — |
| `--list-fonts` | 列出可用字体 | — |

**退出码**：`0` 全部成功；`1` 存在失败；`2` 用法错误。

## DWG 支持

DXF 开箱即用。DWG 是 Autodesk 私有格式，需要一个外部转换器做 DWG→DXF。
Windows 发布包**内置 LibreDWG 便携版**（`libredwg/` 目录，GPL-3.0，见 LIBREDWG-NOTICE.md），
其它平台按以下顺序自动探测，**任一失败自动尝试下一个**：

1. `DWG2PDF_CONVERTER` 环境变量指定的可执行文件
2. 随包内置的 LibreDWG `dwg2dxf`（Windows 包自带；其它平台可放到程序同目录 `libredwg/`）
3. PATH 中的 `dwg2dxf`
4. 系统安装的 ODA File Converter（免费，兼容性最好）

> **为什么需要两个转换器**：实测发现两类 DWG 各有一个转换器搞不定——
> LibreDWG 生成的 DWG（如测试夹具）ODA 会拒转；而天正建筑等国产软件绘制的
> 真实图纸含自定义代理实体（`TCH_*`、`ACAD_PROXY_ENTITY`），LibreDWG 解析失败、
> 只有 ODA 能转。回退链让两种来源的图纸都能转换。

## 字库替换

内置字体是 **Noto Sans SC 子集**（SIL Open Font License 1.1，可自由分发），
含 7032 个字符：ASCII + GB2312 一二级汉字 + 工程常用符号。

换成自己的字体（例如公司标准字体）：

```bash
# 把你的 TTF 放进一个目录
dwg2pdf drawing.dwg --font-dir "D:/cad-fonts"

# 或用环境变量
export DWG2PDF_FONT_DIR="D:/cad-fonts"
```

查找顺序：`--font-dir` → 程序同目录 `fonts/` → 系统字体目录（`C:\Windows\Fonts`、
`/usr/share/fonts`、`/System/Library/Fonts` 等）。

程序会把 CAD 里的字体名（`simhei`、`gbcbig.shx`、`宋体` 等）自动映射到可用字体。

## 性能参考

| 图纸规模 | Python 原版 | Go 版 |
|---|---|---|
| 38.5 MB DXF，1.5 万实体，2447 块 | 1465 秒 | **1.0 秒** |
| 5 MB DWG（含外部转换） | 1465 秒 | **5.2 秒** |

渲染成本与矢量路径数量成正比。超大图纸建议先 `--paper a3` 缩小输出。

## 从源码开发

```bash
git clone https://github.com/huanxiang666/dwg2pdf.git
cd dwg2pdf
go test ./...            # 单元测试
go build -o dwg2pdf ./cmd/dwg2pdf
```

构建发布包（含字体子集）：

```bash
python scripts/make_subset.py path/to/NotoSansSC.ttf internal/font/assets/default.ttf
go build -ldflags "-s -w" -o dist/dwg2pdf ./cmd/dwg2pdf
```

## 技术栈与许可

| 组件 | 用途 | 许可 |
|---|---|---|
| [Noto Sans SC](https://fonts.google.com/noto)（子集，内嵌） | 中文渲染 | SIL OFL 1.1 |
| [golang.org/x/image](https://pkg.go.dev/golang.org/x/image) | 字体解析 | BSD-3-Clause |
| [GNU LibreDWG](https://www.gnu.org/software/libredwg/)（可选，外部进程） | DWG→DXF | GPL-3.0 |
| [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)（可选，外部进程） | DWG→DXF | 免费闭源 |

本软件以 **AGPL-3.0** 发布，与原项目许可一致。外部转换器均为独立进程调用，不改变其各自许可。

## 已知限制

- 不解析 `.ctb/.stb` 打印样式文件
- 图为 2D 平面投影，不做 3D 视图投影
- 图案填充按边界斜线近似，不支持 AutoCAD `.pat` 图案库的精确复现
- 光栅图像（IMAGE 实体）暂不嵌入

## 致谢

本项目的转换语义、参数设计与测试语料来自原 Python 版
[dwg2pdf](https://github.com/huanxiang666/dwg2pdf)。
