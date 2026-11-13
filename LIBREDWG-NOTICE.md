# LibreDWG 捆绑说明

Windows 发布包内 `libredwg/` 目录包含 GNU LibreDWG 0.14 的官方预编译组件
（`dwg2dxf.exe`、`libredwg-0.dll`、`libiconv-2.dll`），用作 DWG→DXF 转换器之一。

- 项目：https://www.gnu.org/software/libredwg/
- 源码：https://github.com/LibreDWG/libredwg
- 许可证：**GPL-3.0-or-later**（见 https://github.com/LibreDWG/libredwg/blob/master/COPYING）
- 获取方式：release 构建时从 LibreDWG 官方 GitHub Release 下载
  `libredwg-0.14-win64.zip` 并仅提取上述三个文件。

本软件（dwg2pdf，AGPL-3.0）通过**独立进程**调用 dwg2dxf，不构成代码级链接；
但随包再分发其二进制需遵守 GPL-3 的再分发义务（提供对应源码获取途径，即上游仓库）。

转换器优先级：`DWG2PDF_CONVERTER` 环境变量 → 随包 LibreDWG → PATH 中的 dwg2dxf
→ 系统安装的 ODA File Converter。任一失败自动尝试下一个，因此
LibreDWG 生成的 DWG 与 AutoCAD/天正等真实 DWG 都能转换。
