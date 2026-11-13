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
- **开箱即用的中文字体**：内置 Apache-2.0 的 Droid Sans Fallback（覆盖简繁中文/日文/韩文）；引用字体缺失时自动替换（simsun/simhei/hztxt.shx/gbcbig.shx 等 30 个常见 CAD 字体名），系统里有真字体时绝不覆盖
- **自带字体库导入**：`--font-dir`（可多次）或 GUI「字体库」添加自己的 SHX/TTF 目录，递归扫描
- **质量/效率档位**：`--quality fast|balanced|high`（效率版/标准/质量版，控制曲线细分精度）
- **大图多线程**：`--threads N` 并行分块渲染单张大图（实测 8 核机器 189s → 83s）
- **安装包**：Linux `.deb`（内置 DWG 转换引擎，装完即用）；Windows NSIS 安装程序（开始菜单/卸载/静默安装 `/S`）；另提供免安装 zip/tar.gz
- **真实语料验证**：以 LibreDWG 测试集 60 个真实 DWG 实测 100% 完成转换

## 下载安装

### 方式一：下载可执行包（推荐，无需 Python）

到 [Releases](https://github.com/huanxiang666/dwg2pdf/releases) 下载：

| 平台 | 文件 | 用法 |
|---|---|---|
| Windows x64 | `dwg2pdf-x.y.z-windows-x64-setup.exe` | **安装程序**：开始菜单快捷方式、可卸载、支持静默安装 `/S` |
| Windows x64 | `dwg2pdf-x.y.z-windows-x64.zip` | 免安装绿色版：解压后双击 `dwg2pdf-gui.exe` 或用 `dwg2pdf.exe` |
| Linux x64 | `dwg2pdf_x.y.z_amd64.deb` | **安装包**：`sudo apt install ./dwg2pdf_x.y.z_amd64.deb`，命令行直接 `dwg2pdf` / 应用菜单启动 GUI |
| Linux x64 | `dwg2pdf-x.y.z-linux-x64.tar.gz` | 免安装版：解压后运行 `dwg2pdf/dwg2pdf` |
| macOS arm64 | `dwg2pdf-x.y.z-macos-arm64-experimental.tar.gz` | 实验性构建（未内置 DWG 引擎，需自装 ODA/libredwg） |

每个资产附带 `.sha256` 校验和。

> Windows 包未做代码签名，首次运行 SmartScreen 提示时选择「仍要运行」。

### 方式二：pip 安装（需要 Python 3.10+）

```bash
pip install git+https://github.com/huanxiang666/dwg2pdf.git
dwg2pdf --check     # 环境自检
dwg2pdf-gui         # 图形界面
```

## DWG 支持

**Linux 的 .deb / tar.gz 已内置 LibreDWG 0.14 转换引擎，DWG 开箱即用。**
Windows / macOS / pip 安装版需要一个免费外部转换器（自动检测，也可用 `DWG2PDF_CONVERTER=/path/to/exe` 指定）：

**DXF 文件任何平台都开箱即用**。DWG 是 Autodesk 私有格式，需要以下**任一**转换器（Linux 发行包已内置 ②）：

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
| `--quality` | `fast`（效率版）/ `balanced`（标准）/ `high`（质量版） | `balanced` |
| `--threads` | 单张大图分块渲染的并行线程数（0=自动；仅 `-j 1` 时生效） | `0` |
| `--converter-timeout` | 单个 DWG 外部转换超时（秒） | `600` |

**退出码**：`0` 全部成功；`1` 存在失败；`2` 用法错误。

## 转换行为说明（工程细节）

- **布局选择**：`auto` 模式下含实体的图纸空间布局优先（即"打印布局"语义），全部为空布局时回退模型空间。
- **纸张与比例**：图纸空间按图纸自身纸张尺寸 + CAD 内设置的打印比例出图（等效在 CAD 里点打印）；模型空间按内容范围自动定纸张。指定 `--paper` 时内容缩放到该纸张。
- **字体**：文字按 DXF 引用的字体渲染；系统缺少对应 SHX/TTF 时使用替代字体（笔画形状近似）。把 CAD 字体目录用 `--font-dir` 传入（或装到系统字体目录）可获得更高保真度。
- **退化几何**：内容为空或坐标损坏（NaN）的布局输出空白页并在日志/报告中给出 WARN——文件本身的问题被如实暴露，而不是静默产出坏 PDF。
- **3D**：以 2D 图纸（平面投影）方式渲染，与 CAD 的"打印"行为一致；不做 3D 视图投影。

## v0.3.0：看图级 GUI 体验

**图纸预览（像 CAD 看图软件）**
- 工具栏「👁 预览图纸」或双击文件：矢量图纸栅格化显示（自研 Pillow 后端，零额外依赖）
- 滚轮缩放（以光标为中心）、右键拖动平移
- 57MB / 34k 记录实测：预览全图 15s（其中 DXF 解析 10s，渲染仅 1.3s）

**框选区域导出（打印局部详图）**
- 「✂ 框选导出」→ 在预览上拖出矩形 → 保存为该区域的高清矢量 PDF → 一键「打印 / 打开 / 打开目录」
- 坐标精确往返（实测误差 < 0.01 图纸单位）；只裁剪选区内图元（34337 → 3958），导出 6~8s
- DXF 文档单条目缓存：同一图纸连续导出多块区域不重复解析

**引擎助手（Windows 用户装 DWG 引擎不再看 README）**
- 工具栏「⚙ 引擎助手」：列出全部下载源（LibreDWG 官方 GitHub + 3 个加速镜像 + Go 高速核心）
- 「① 测速」并行实测各源速度并自动选中最快（实测镜像比官方直连快 5 倍）
- 「② 下载并安装」：4 线程 HTTP Range 分片下载（实测 13MB 引擎 4.7s，峰值 5MB/s）、断点续传（取消/断网后重试接着下）、zip 完整性校验、自动解压装配到用户目录，装完即刻被 `detect_converter` 识别
- ODA 官网下载已改为需登录，助手提供「打开官网」引导

**Go 高速核心（可选增强，实验性）**
- 本仓库另一条发布线的 Go 渲染核心（DXF 秒级转换、文字可搜索）可在引擎助手里一键下载
- 选项面板「渲染核心」切到 Go；任何失败自动回退内置渲染器并记录警告

**检查更新（手动触发）**
- 工具栏「🔄 检查更新」：查询 GitHub Releases，按资产命名精确匹配本 GUI 版本线（不会把 Go CLI 版误报为你的更新），显示新版本号/说明/下载页链接；绝不自动联网

**状态栏升级**
- 转换进度 + 当前文件分块进度 + 已用时 + **预计剩余时间**（滚动均值）
- **CPU 占用率**实时显示（Linux /proc/stat，Windows GetSystemTimes，纯标准库）
- DWG 引擎徽章（✓/未装 ⚠）

**界面**：ttkbootstrap 现代主题（cosmo），不可用时自动降级原生 ttk；三栏布局（文件列表 | 预览画布 | 选项面板）。

### 关于 GPU 加速（诚实说明）

矢量 PDF 生成是 CPU 密集的 MuPDF 路径写入，整条管线**没有可 GPU 化的环节**，因此不提供假的 GPU 开关。`dwg2pdf --check` 会如实报告检测到的 GPU 并注明其不参与渲染。真正的加速手段：`-j`（多文件并行）、`--threads`（大图分块并行，实测 189s→83s）、`--quality fast`、Go 高速核心（DXF 场景）。

## 字体系统（v0.2.0）

- **默认就能出中文**：安装包内置 Droid Sans Fallback Full（Apache-2.0，4MB，覆盖简繁中日韩）。图纸引用的字体在系统里不存在时，自动用它替换渲染——中文不再显示为方框（tofu）。
- **30 个常见 CAD 字体名自动替换**：simsun/simhei/simkai/msyh/dengxian 等 TTF 名，hztxt.shx/gbcbig.shx/gbenor.shx/tssdeng.shx 等 SHX 大字体名。**系统或用户字体库里有真字体时永远优先用真字体**，替换只发生在缺失时。
- **导入自己的字体库**：`--font-dir /path/to/fonts`（可重复），或 GUI「字体库 → 添加…」；目录递归扫描 TTF/OTF/TTC/SHX/SHP/LFF。把 CAD 安装目录的 Fonts 文件夹指过来即可获得与 CAD 一致的字体效果。
- `dwg2pdf --check` 显示字体子系统状态（内置字体、注册的替换别名、可见字体数）。

## 质量档位与加速（v0.2.0）

| 档位 | 说明 | 适用 |
|---|---|---|
| `--quality fast` | 效率版：圆弧 36 段、粗平坦化，文件更小、渲染更快 | 快速预览、批量出图 |
| `--quality balanced` | 标准（默认）：圆弧 128 段 | 日常交付 |
| `--quality high` | 质量版：圆弧 512 段、精细平坦化 | 高精度归档、放大审阅 |

**加速手段（真实有效）**：
- `-j N`：多文件并行（不同文件同时转）
- `--threads N`：单张大图的分块并行渲染——实测 8 核机器上 41k 实体建施图 **189s → 83s（2.3x）**，输出与串行完全一致（同页数/同画幅/同路径数）

**关于 GPU 加速**：矢量 PDF 生成是 CPU 密集的 MuPDF 路径写入，整条管线没有可 GPU 化的环节，因此**不提供假的 GPU 开关**。`--check` 会如实报告检测到的 GPU 并注明其不参与渲染。需要更快请用上面的多核参数，或 `--quality fast`。

## 大图纸渲染：分块提交（v0.1.3）

PyMuPDF 后端把整张图累积到**一个** shape 里，最后一次性 commit；曲线越多，这次 commit 是**超线性**的。实测（85,364 条记录 / 154 万图元）：

| 策略 | 结果 |
|---|---|
| 单次 commit（旧） | **15 分钟以上未完成**，进程 100% CPU、内存稳定，看起来像卡死 |
| 分块提交（现） | **189 秒完成**，11 页同画幅输出 |

因此当布局实体数 ≥ `--chunk-threshold`（默认 20000）时，自动走分块路径：按 `--chunk-records`（默认 8000）条记录分批 commit，**每批一页、画幅与坐标系完全一致**（同一变换矩阵），几何零丢失。

- 这类图纸会输出成多页（例如上面的建筑图 11 页），这是分块提交的必然结果：**内容按记录顺序分布在各页**，不是缩小视图。
- `--chunk-threshold 0` 可强制所有图纸走分块；`--chunk-threshold 999999999` 可关闭。
- 报告与 `[warn]` 会明确写出「N entities rendered in M same-frame page(s)」。

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
- LibreDWG 偶尔会写出损坏的 `SORTENTSTABLE` 对象（实测某 5.3MB r2007 建施图：36733 个条目中缺一个排序句柄，ezdxf 严格/容错读取都会致命报错）。dwg2pdf 已内置宽容加载器自动跳过损坏条目——该对象只是 AutoCAD 绘制顺序的记录，不影响渲染内容。
