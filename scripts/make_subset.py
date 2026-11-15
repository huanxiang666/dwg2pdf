#!/usr/bin/env python
"""生成内置字体子集。

用法: python make_subset.py <源TTF> <目标TTF>

默认子集包含:
  - ASCII 可打印字符
  - GB2312 常用汉字（一级 + 二级）
  - 工程图纸常见符号（直径、度、公差、罗马数字、单位等）

目标是把 17MB 的完整思源黑体压到 1-3MB，
这样安装包才可能控制在合理体积。
"""
import sys
from fontTools import subset
from fontTools.ttLib import TTFont


def gb2312_chars():
    """返回 GB2312 编码空间内的全部汉字。"""
    out = set()
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            out.add(ch)
    return out


def main():
    if len(sys.argv) < 3:
        print("用法: python make_subset.py <源TTF> <目标TTF>", file=sys.stderr)
        return 1
    src, dst = sys.argv[1], sys.argv[2]

    chars = set()

    # ASCII
    chars.update(chr(c) for c in range(0x20, 0x7F))

    # GB2312 汉字
    chars |= gb2312_chars()

    # 工程常用符号
    chars |= set(
        "　、。〈〉《》「」『』【】〔〕〖〗〘〙〚〛〜〝〞〟–—‘’“”„‟…‧"
        "！＂＃＄％＆＇（）＊＋，－．／０１２３４５６７８９：；＜＝＞？＠"
        "［＼］＾＿｀｛｜｝～"
        "℃°′″±×÷≤≥≠≈∞√∑∏∫∮∟∠⊥∥△▲▽▼○●◇◆□■☆★"
        "㎡㎥㎝㎜㎞㎏㏑ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"
        "αβγδεζηθικλμνξοπρστυφχψω"
        "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
    )

    font = TTFont(src)
    cmap = font.getBestCmap()
    keep = {c for c in chars if ord(c) in cmap}
    print(f"原字体字符数: {len(cmap)}, 子集保留: {len(keep)}")

    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.name_legacy = True
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.drop_tables += ["DSIG"]
    opts.hinting = True

    font = subset.load_font(src, opts)
    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(text="".join(sorted(keep)))
    subsetter.subset(font)
    subset.save_font(font, dst, opts)

    import os
    print(f"输出: {dst}  {os.path.getsize(dst)/1024/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
