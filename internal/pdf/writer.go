// Package pdf 是一个极简、零依赖的矢量 PDF 写入器。
// 只实现图纸输出所需的最小子集：路径、填充、描边、文字、页面树。
package pdf

import (
	"bufio"
	"bytes"
	"compress/zlib"
	"fmt"
	"io"
	"math"
	"os"
	"strings"
)

// Writer 生成 PDF 文档。
type Writer struct {
	pages   []*Page
	fonts   []*Font
	fontIdx map[string]int
}

// New 创建 Writer。
func New() *Writer {
	return &Writer{fontIdx: make(map[string]int)}
}

// Page 表示一页。
type Page struct {
	w, h      float64
	stream    bytes.Buffer
	gidLookup func(fontIdx int, r rune) uint16
	// toUni 记录 (字体索引, GID) -> Unicode，用于生成 ToUnicode CMap
	toUni map[[2]uint32]rune
}

// AddPage 添加页面（单位：PDF point, 1/72 inch）。
func (w *Writer) AddPage(width, height float64) *Page {
	p := &Page{w: width, h: height, toUni: make(map[[2]uint32]rune)}
	w.pages = append(w.pages, p)
	return p
}

// Content 返回内容流。
func (p *Page) Content() *bytes.Buffer { return &p.stream }

// Font 是一个内嵌字体资源。
type Font struct {
	name string
	data []byte
}

// AddFont 注册一个 TrueType 字体（内嵌完整字体数据）。
func (w *Writer) AddFont(name string, ttfData []byte) int {
	if i, ok := w.fontIdx[name]; ok {
		return i
	}
	if ttfData == nil || len(ttfData) < 12 {
		if i, ok := w.fontIdx[""]; ok {
			return i
		}
		i := len(w.fonts)
		w.fonts = append(w.fonts, &Font{name: name})
		w.fontIdx[""] = i
		w.fontIdx[name] = i
		return i
	}
	f := &Font{name: name, data: ttfData}
	i := len(w.fonts)
	w.fonts = append(w.fonts, f)
	w.fontIdx[name] = i
	return i
}

// fmtF 数字格式化。
func fmtF(v float64) string {
	if v == float64(int64(v)) {
		return fmt.Sprintf("%d", int64(v))
	}
	return fmt.Sprintf("%.4f", v)
}

// Line 绘制线段。
func (p *Page) Line(x1, y1, x2, y2 float64) {
	fmt.Fprintf(&p.stream, "%s %s m %s %s l S\n", fmtF(x1), fmtF(y1), fmtF(x2), fmtF(y2))
}

// StrokeColor 设置描边色。
func (p *Page) StrokeColor(r, g, b float64) {
	fmt.Fprintf(&p.stream, "%s %s %s RG\n", fmtF(r), fmtF(g), fmtF(b))
}

// FillColor 设置填充色。
func (p *Page) FillColor(r, g, b float64) {
	fmt.Fprintf(&p.stream, "%s %s %s rg\n", fmtF(r), fmtF(g), fmtF(b))
}

// SetLineWidth 设置线宽（PDF 用户空间单位，会随 cm 一起缩放）。
func (p *Page) SetLineWidth(wd float64) {
	if wd <= 0 || math.IsNaN(wd) {
		return
	}
	if wd < 0.001 {
		wd = 0.001
	}
	fmt.Fprintf(&p.stream, "%s w\n", fmtF(wd))
}

// Save 保存图形状态。
func (p *Page) Save() { p.stream.WriteString("q\n") }

// Restore 恢复图形状态。
func (p *Page) Restore() { p.stream.WriteString("Q\n") }

// Transform 应用变换矩阵。
func (p *Page) Transform(a, b, c, d, e, f float64) {
	fmt.Fprintf(&p.stream, "%s %s %s %s %s %s cm\n", fmtF(a), fmtF(b), fmtF(c), fmtF(d), fmtF(e), fmtF(f))
}

// MoveTo 移动到点。
func (p *Page) MoveTo(x, y float64) {
	fmt.Fprintf(&p.stream, "%s %s m\n", fmtF(x), fmtF(y))
}

// LineTo 画线到点。
func (p *Page) LineTo(x, y float64) {
	fmt.Fprintf(&p.stream, "%s %s l\n", fmtF(x), fmtF(y))
}

// CurveTo 三次贝塞尔。
func (p *Page) CurveTo(x1, y1, x2, y2, x3, y3 float64) {
	fmt.Fprintf(&p.stream, "%s %s %s %s %s %s c\n", fmtF(x1), fmtF(y1), fmtF(x2), fmtF(y2), fmtF(x3), fmtF(y3))
}

// ClosePath 闭合路径。
func (p *Page) ClosePath() { p.stream.WriteString("h\n") }

// Stroke 描边。
func (p *Page) Stroke() { p.stream.WriteString("S\n") }

// Fill 填充（非零环绕规则）。
func (p *Page) Fill() { p.stream.WriteString("f\n") }

// FillEvenOdd 填充（奇偶规则），用于带孔洞的复合路径。
func (p *Page) FillEvenOdd() { p.stream.WriteString("f*\n") }

// FillStrokeEvenOdd 填充并描边（奇偶规则）。
func (p *Page) FillStrokeEvenOdd() { p.stream.WriteString("B*\n") }

// FillStroke 填充并描边。
func (p *Page) FillStroke() { p.stream.WriteString("B\n") }

// Circle 用 4 段贝塞尔画圆（不自动描边）。
func (p *Page) Circle(cx, cy, r float64) {
	const k = 0.5522847498307936
	kr := k * r
	p.MoveTo(cx+r, cy)
	p.CurveTo(cx+r, cy+kr, cx+kr, cy+r, cx, cy+r)
	p.CurveTo(cx-kr, cy+r, cx-r, cy+kr, cx-r, cy)
	p.CurveTo(cx-r, cy-kr, cx-kr, cy-r, cx, cy-r)
	p.CurveTo(cx+kr, cy-r, cx+r, cy-kr, cx+r, cy)
	p.ClosePath()
}

// Arc 画圆弧（角度为弧度）。
func (p *Page) Arc(cx, cy, r, start, end float64) {
	for end < start {
		end += 2 * math.Pi
	}
	delta := end - start
	if delta <= 0 {
		return
	}
	segs := int(math.Ceil(delta / (math.Pi / 2)))
	if segs < 1 {
		segs = 1
	}
	step := delta / float64(segs)
	k := 4.0 / 3.0 * math.Tan(step/4)
	a0 := start
	p.MoveTo(cx+r*math.Cos(a0), cy+r*math.Sin(a0))
	for i := 0; i < segs; i++ {
		a1 := a0 + step
		x0, y0 := cx+r*math.Cos(a0), cy+r*math.Sin(a0)
		x1, y1 := cx+r*math.Cos(a1), cy+r*math.Sin(a1)
		c1x := x0 - k*r*math.Sin(a0)
		c1y := y0 + k*r*math.Cos(a0)
		c2x := x1 + k*r*math.Sin(a1)
		c2y := y1 - k*r*math.Cos(a1)
		p.CurveTo(c1x, c1y, c2x, c2y, x1, y1)
		a0 = a1
	}
}

// Text 在 (x,y) 处绘制文字（Type0/Identity-H）。
// 若设置了 GlyphLookup，则每个字符先转成 GlyphID 再编码。
func (p *Page) Text(fontIdx int, size float64, x, y float64, s string) {
	if s == "" {
		return
	}
	const hd = "0123456789ABCDEF"
	var hex strings.Builder
	hex.Grow(len(s) * 4)
	for _, r := range s {
		var v uint16
		if p.gidLookup != nil {
			v = p.gidLookup(fontIdx, r)
			// 记录反向映射，供生成 ToUnicode 使用
			if p.toUni != nil {
				p.toUni[[2]uint32{uint32(fontIdx), uint32(v)}] = r
			}
		} else {
			v = uint16(r)
			if v > 0xFFFF {
				v = '?'
			}
		}
		hex.WriteByte(hd[v>>12&0xF])
		hex.WriteByte(hd[v>>8&0xF])
		hex.WriteByte(hd[v>>4&0xF])
		hex.WriteByte(hd[v&0xF])
	}
	fmt.Fprintf(&p.stream, "BT /F%d %s Tf 1 0 0 1 %s %s Tm <%s> Tj ET\n",
		fontIdx, fmtF(size), fmtF(x), fmtF(y), hex.String())
}

// SetGlyphLookup 设置字形查找回调。
func (p *Page) SetGlyphLookup(fn func(fontIdx int, r rune) uint16) {
	p.gidLookup = fn
}

// buildToUnicode 生成 ToUnicode CMap 内容流。
// 把所有页面中该字体的 (GID -> Unicode) 映射写成 bfchar 块。
func buildToUnicode(pages []*Page, fontIdx int) string {
	seen := make(map[uint16]rune, 2048)
	for _, p := range pages {
		if p.toUni == nil {
			continue
		}
		for k, r := range p.toUni {
			if int(k[0]) != fontIdx {
				continue
			}
			gid := uint16(k[1])
			if _, dup := seen[gid]; !dup {
				seen[gid] = r
			}
		}
	}

	var b strings.Builder
	b.WriteString("/CIDInit /ProcSet findresource begin\n")
	b.WriteString("12 dict begin\nbegincmap\n")
	b.WriteString("/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n")
	b.WriteString("/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n")
	b.WriteString("1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n")

	gids := make([]uint16, 0, len(seen))
	for g := range seen {
		gids = append(gids, g)
	}
	sortU16(gids)

	// bfchar 每块最多 100 条
	const chunk = 100
	for start := 0; start < len(gids); start += chunk {
		end := start + chunk
		if end > len(gids) {
			end = len(gids)
		}
		fmt.Fprintf(&b, "%d beginbfchar\n", end-start)
		for _, g := range gids[start:end] {
			r := seen[g]
			if r > 0xFFFF {
				r = '?'
			}
			fmt.Fprintf(&b, "<%04X> <%04X>\n", g, uint16(r))
		}
		b.WriteString("endbfchar\n")
	}
	b.WriteString("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
	return b.String()
}

// sortU16 简单的插入/快排。
func sortU16(a []uint16) {
	for i := 1; i < len(a); i++ {
		for j := i; j > 0 && a[j] < a[j-1]; j-- {
			a[j], a[j-1] = a[j-1], a[j]
		}
	}
}

// Save 写出 PDF 文件。
func (w *Writer) Save(path string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return w.WritePDF(f)
}

// WriteTo 将 PDF 写入 io.Writer。
func (w *Writer) WritePDF(out io.Writer) error {
	bw := bufio.NewWriterSize(out, 1<<20)
	defer bw.Flush()

	offsets := make(map[int]int64, 16+len(w.fonts)*4+len(w.pages)*2)
	pos := int64(0)
	write := func(s string) {
		n, _ := bw.WriteString(s)
		pos += int64(n)
	}
	writeBytes := func(b []byte) {
		n, _ := bw.Write(b)
		pos += int64(n)
	}
	obj := func(num int, body string) {
		offsets[num] = pos
		write(fmt.Sprintf("%d 0 obj\n", num))
		write(body)
		write("endobj\n")
	}

	write("%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")

	numFonts := len(w.fonts)
	fontBase := 3
	pageBase := fontBase + numFonts*5

	obj(1, "<< /Type /Catalog /Pages 2 0 R >>\n")

	var kids bytes.Buffer
	for i := range w.pages {
		fmt.Fprintf(&kids, "%d 0 R ", pageBase+i*2)
	}
	obj(2, fmt.Sprintf("<< /Type /Pages /Count %d /Kids [%s] >>\n", len(w.pages), kids.String()))

	for i, f := range w.fonts {
		base := fontBase + i*5
		var comp bytes.Buffer
		zw, _ := zlib.NewWriterLevel(&comp, zlib.BestCompression)
		zw.Write(f.data)
		zw.Close()

		offsets[base+3] = pos
		write(fmt.Sprintf("%d 0 obj\n", base+3))
		write(fmt.Sprintf("<< /Length %d /Length1 %d /Filter /FlateDecode >>\nstream\n", comp.Len(), len(f.data)))
		writeBytes(comp.Bytes())
		write("\nendstream\nendobj\n")

		// ToUnicode CMap：把所有页面的 GID->Unicode 映射合并
		cm := buildToUnicode(w.pages, i)
		obj(base+4, fmt.Sprintf("<< /Length %d >>\nstream\n%s\nendstream\n", len(cm), cm))

		obj(base+2, fmt.Sprintf(
			"<< /Type /FontDescriptor /FontName /F%d /Flags 4 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 /FontFile2 %d 0 R >>\n",
			i, base+3))

		// CIDFontType2：CIDToGIDMap /Identity 表示 CID == GID。
		// 我们的 Text() 已把字符转成 GID，因此这是正确的。
		obj(base+1, fmt.Sprintf(
			"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /F%d /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor %d 0 R /DW 1000 /CIDToGIDMap /Identity >>\n",
			i, base+2))

		obj(base, fmt.Sprintf(
			"<< /Type /Font /Subtype /Type0 /BaseFont /F%d /Encoding /Identity-H /DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>\n",
			i, base+1, base+4))
	}

	for i, pg := range w.pages {
		pageNum := pageBase + i*2
		contentNum := pageNum + 1

		var comp bytes.Buffer
		zw, _ := zlib.NewWriterLevel(&comp, zlib.BestSpeed)
		zw.Write(pg.stream.Bytes())
		zw.Close()

		offsets[contentNum] = pos
		write(fmt.Sprintf("%d 0 obj\n", contentNum))
		write(fmt.Sprintf("<< /Length %d /Filter /FlateDecode >>\nstream\n", comp.Len()))
		writeBytes(comp.Bytes())
		write("\nendstream\nendobj\n")

		var res bytes.Buffer
		res.WriteString("<< /Font << ")
		for fi := range w.fonts {
			fmt.Fprintf(&res, "/F%d %d 0 R ", fi, fontBase+fi*5)
		}
		res.WriteString(">> >>")

		obj(pageNum, fmt.Sprintf(
			"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Contents %d 0 R /Resources %s >>\n",
			fmtF(pg.w), fmtF(pg.h), contentNum, res.String()))
	}

	maxObj := pageBase + len(w.pages)*2 - 1
	// 字体对象实际最大编号是 fontBase+numFonts*5-1，若没有页面则用它
	if fo := fontBase + numFonts*5 - 1; fo > maxObj {
		maxObj = fo
	}
	xrefPos := pos
	write(fmt.Sprintf("xref\n0 %d\n", maxObj+1))
	write("0000000000 65535 f \n")
	for n := 1; n <= maxObj; n++ {
		o := offsets[n]
		write(fmt.Sprintf("%010d 00000 n \n", o))
	}
	write(fmt.Sprintf("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", maxObj+1, xrefPos))
	return nil
}
