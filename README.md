# dwg2pdf — DWG/DXF 批量转 PDF 工具

[![CI](https://github.com/huanxiang666/dwg2pdf/actions/workflows/ci.yml/badge.svg)](https://github.com/huanxiang666/dwg2pdf/actions/workflows/ci.yml)
[![Release](https://github.com/huanxiang666/dwg2pdf/actions/workflows/release.yml/badge.svg)](https://github.com/huanxiang666/dwg2pdf/actions/workflows/release.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

把 CAD 图纸（**DWG / DXF**）批量转换成**矢量 PDF** 的跨平台工具，支持 **Windows** 和 **Linux**，提供命令行（CLI）和图形界面（GUI）两种用法。

English: see [README_EN.md](README_EN.md)

## 特性

- **批量转换**：一次传入多个文件或整个目录（可递归子目录），多进程并行，一个文件失败不影响整批
- **矢量 PDF 输出**：基于 ezdxf + PyMuPDF 渲染，无限放大不模糊，适合打印、归档、算量
- **尊重 CAD 打印语义**：
  - 图纸空间（布局）按图纸自带的纸张尺寸和打印比例出图
  - 模型空间自动检测内容范围适配纸张（上限 ISO A0）
  - 多个布局可合并导出为多页 PDF
- **实用选项**：单色（黑白打印）、指定纸张（ISO A0–A4 / ANSI / ARCH / Letter / Legal）、缩放、页边距、跳过已存在的 PDF
- **双界面**：命令行 + 图形界面（打包版内置，双击即用）
- **诚实的失败处理**：JSON 批量报告、明确退出码；几何退化的文件输出空白页并给出显式 WARN，不静默吞掉
- **真实语料验证**：以 LibreDWG 测试集 60 个真实 DWG 实测 100% 完成转换

## 下载安装

### 方式一：下载可执行包（推荐，无需 Python）

到 [Releases](https://github.com/huanxiang666/dwg2pdf/releases) 下载：

| 平台 | 文件 | 用法 |
|---|---|---|
| Windows x64 | `dwg2pdf-x.y.z-windows-x64.zip` | 解压后双击 `dwg2pdf-gui.exe`（图形界面）或用 `dwg2pdf.exe`（命令行） |
| Linux x64 | `dwg2pdf-x.y.z-linux-x64.tar.gz` | 解压后运行 `dwg2pdf/dwg2pdf --gui`（图形界面）或 `dwg2pdf/dwg2pdf`（命令行） |

> Windows 包未做代码签名，首次运行 SmartScreen 提示时选择「仍要运行」。

### 方式二：pip 安装（需要 Python 3.10+）

```bash
pip install git+https://github.com/huanxiang666/dwg2pdf.git
dwg2pdf --check     # 环境自检
dwg2pdf-gui         # 图形界面
```

## DWG 支持：需要一个免费转换器

**DXF 文件开箱即用**。DWG 是 Autodesk 私有格式，本工具通过调用以下**任一**免费外部转换器完成 DWG → DXF（自动检测，也可用环境变量 `DWG2PDF_CONVERTER=/path/to/exe` 手动指定）：

1. **ODA File Converter**（Open Design Alliance 官方免费工具，Windows / Linux / macOS）
   下载：<https://www.opendesign.com/guestfiles/oda_file_converter>，安装即可被自动识别。
2. **GNU LibreDWG 的 `dwg2dxf`**
   - Debian / Ubuntu：`sudo apt install libredwg-tools`
   - 其他发行版 / Windows：源码编译 <https://www.gnu.org/software/libredwg/>

装好后运行 `dwg2pdf --check` 确认状态。两者都没有时，DWG 文件会报出带安装指引的明确错误，DXF 转换不受影响。

## 快速上手

### 图形界面

Windows 双击 `dwg2pdf-gui.exe`；Linux 运行 `dwg2pdf --gui`（或 `dwg2pdf-gui`）。
添加文件/文件夹 → 选输出目录和选项 → 开始转换，进度与逐文件结果实时显示。

### 命令行

```bash
# 单文件：PDF 生成在源文件旁边
dwg2pdf drawing.dwg

# 整个目录递归转换，输出到 ./pdf
dwg2pdf ./drawings -r -o ./pdf

# 黑白 A4、8 进程并行
dwg2pdf *.dwg --monochrome --paper a4 -j 8

# 导出全部布局（模型 + 每张图纸布局合成一个多页 PDF），并生成 JSON 报告
dwg2pdf project.dwg --layout all --report report.json

# 环境自检（转换器 / 依赖 / GUI 可用性）
dwg2pdf --check
```

### 参数一览

| 参数 | 说明 | 默认 |
|---|---|---|
| `-o, --output-dir` | 输出目录 | 源文件旁 |
| `-r, --recursive` | 递归扫描目录 | 关 |
| `--layout` | `auto`（非空图纸布局优先，否则模型空间）/ `modelspace` / `all` / 布局名 | `auto` |
| `--monochrome` | 黑白输出 | 关 |
| `--paper` | `auto` 或 ISO A0–A4、ANSI A–E、ARCH C–E1、Letter、Legal（`a4` 等别名可用），横竖向随内容自动选择 | `auto` |
| `--scale` | 内容附加缩放系数 | `1` |
| `--margin` | 页边距（mm） | auto 纸张 0，指定纸张 5 |
| `--fit / --no-fit` | 强制缩放到一页 / 保持比例 | 按布局类型自动 |
| `-j, --jobs` | 并行进程数（0 = CPU 核数） | `0` |
| `--skip-existing` | 目标 PDF 已存在则跳过 | 关 |
| `--font-dir` | 追加字体目录（SHX/TTF），可重复 | — |
| `--report` | 输出 JSON 批量报告 | — |
| `--converter-timeout` | 单个 DWG 外部转换超时（秒） | `600` |

**退出码**：`0` 全部成功；`1` 存在失败；`2` 用法错误。

## 转换行为说明（工程细节）

- **布局选择**：`auto` 模式下含实体的图纸空间布局优先（即"打印布局"语义），全部为空布局时回退模型空间。
- **纸张与比例**：图纸空间按图纸自身纸张尺寸 + CAD 内设置的打印比例出图（等效在 CAD 里点打印）；模型空间按内容范围自动定纸张。指定 `--paper` 时内容缩放到该纸张。
- **字体**：文字按 DXF 引用的字体渲染；系统缺少对应 SHX/TTF 时使用替代字体（笔画形状近似）。把 CAD 字体目录用 `--font-dir` 传入（或装到系统字体目录）可获得更高保真度。
- **退化几何**：内容为空或坐标损坏（NaN）的布局输出空白页并在日志/报告中给出 WARN——文件本身的问题被如实暴露，而不是静默产出坏 PDF。
- **3D**：以 2D 图纸（平面投影）方式渲染，与 CAD 的"打印"行为一致；不做 3D 视图投影。

## 性能预期（实测数据）

渲染成本与图纸的矢量路径数量成正比（ezdxf 的 PDF 后端逐路径写入）。2020 级台式机单核参考值：

| 图纸规模 | 耗时 |
|---|---|
| 普通图纸（< 5 MB DXF） | 秒级 ~ 几十秒 |
| 大型图纸（57 MB DXF，1.4 万实体 + 5.7 千块引用） | 模型空间约 **3 分钟** |
| 同上，图纸布局含 3 个视口（viewport） | 约 **23 分钟**（每个视口都要完整重渲染一遍模型内容） |

- **大图纸首选 `--layout modelspace`**：跳过布局视口的成倍重渲染，通常快一个数量级。
- 转换大文件时 CLI 每 30 秒打印一行 `[heartbeat]` 进度、GUI 底部实时显示已用时——**界面不动不等于卡死**，请耐心等它跑完。
- 中间 DXF 超过 5 MB 会给出「large drawing」警告（`--large-dxf-mb` 可调阈值）。

## 自动化集成示例

```bash
# cron：每天 2 点转换收件目录
0 2 * * * dwg2pdf /data/incoming -r -o /data/pdf --skip-existing --report /data/logs/last.json

# 退出码驱动流水线
dwg2pdf "$1" -o out || { echo "转换失败"; exit 1; }
```

## 从源码开发

```bash
git clone https://github.com/huanxiang666/dwg2pdf.git && cd dwg2pdf
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python tests/make_fixtures.py        # 重新生成测试夹具（可选）
pytest -q                            # 46+ 项测试
```

## 打包发布

```bash
pip install . pyinstaller
pyinstaller packaging/dwg2pdf.spec --noconfirm   # 产物在 dist/dwg2pdf/
```

推送 `v*` 标签（如 `git tag v0.1.0 && git push --tags`）后，GitHub Actions 自动构建 Windows / Linux 可执行包并发布到 Releases（含冒烟测试：打包后的二进制真实转换一份 DXF 并校验 PDF 魔数）。

## 技术栈与许可证

| 组件 | 用途 | 许可证 |
|---|---|---|
| [ezdxf](https://ezdxf.mozman.at/) | DXF 解析与渲染引擎 | MIT |
| [PyMuPDF](https://pymupdf.org/) | PDF 后端 | AGPL-3.0 |
| [LibreDWG](https://www.gnu.org/software/libredwg/)（可选，外部进程） | DWG → DXF | GPL-3.0 |
| [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)（可选，外部进程） | DWG → DXF | 免费闭源 |

本软件整体以 **AGPL-3.0** 发布（因捆绑 PyMuPDF），详见 [LICENSE](LICENSE)。外部转换器均为独立进程调用，不改变其各自许可。

## 已知限制

- 不解析 `.ctb/.stb` 打印样式文件；颜色按实体自身颜色（`--monochrome` 可全黑）。
- ODA File Converter 在 Linux 上需要 Qt 运行库（发行版包一般自带）。
- 光栅图像（IMAGE 实体）按显示策略嵌入，超大幅面图像会增大 PDF 体积。
- `--converter-timeout` 只约束 LibreDWG `dwg2dxf`；ODA File Converter 的上游接口不提供超时参数，若 ODA 进程卡死会占住对应 worker（其余文件不受影响，可 `--jobs 1` 规避或改用 LibreDWG）。
