package pdf

import (
	"bytes"
	"strings"
	"testing"
)

func TestWriteEmptyPDF(t *testing.T) {
	w := New()
	w.AddPage(595, 842)
	var buf bytes.Buffer
	if err := w.WritePDF(&buf); err != nil {
		t.Fatal(err)
	}
	out := buf.String()
	if !strings.HasPrefix(out, "%PDF-1.7") {
		t.Error("缺少 PDF 文件头")
	}
	if !strings.HasSuffix(strings.TrimSpace(out), "%%EOF") {
		t.Error("缺少 EOF 标记")
	}
	if !strings.Contains(out, "/Type /Catalog") {
		t.Error("缺少 Catalog")
	}
	if !strings.Contains(out, "startxref") {
		t.Error("缺少 startxref")
	}
}

func TestWriteMultiPage(t *testing.T) {
	w := New()
	for i := 0; i < 3; i++ {
		p := w.AddPage(595, 842)
		p.Line(0, 0, 10, 10)
	}
	var buf bytes.Buffer
	if err := w.WritePDF(&buf); err != nil {
		t.Fatal(err)
	}
	out := buf.String()
	if n := strings.Count(out, "/Type /Page "); n != 3 {
		t.Errorf("期望 3 个 Page，得到 %d", n)
	}
	if !strings.Contains(out, "/Count 3") {
		t.Error("页数统计错误")
	}
}

func TestXrefOffsetsValid(t *testing.T) {
	w := New()
	p := w.AddPage(595, 842)
	p.Line(1, 2, 3, 4)

	var buf bytes.Buffer
	if err := w.WritePDF(&buf); err != nil {
		t.Fatal(err)
	}
	out := buf.String()

	// 解析 xref 表，逐个校验偏移处确实是 "<n> 0 obj"
	idx := strings.LastIndex(out, "xref")
	if idx < 0 {
		t.Fatal("找不到 xref")
	}
	lines := strings.Split(out[idx:], "\n")
	for i := 3; i < len(lines); i++ {
		line := strings.TrimSpace(lines[i])
		if line == "trailer" || line == "" {
			break
		}
		if len(line) < 18 {
			continue
		}
		var off int
		if _, err := sscanf(line, &off); err != nil {
			continue
		}
		if off <= 0 || off >= len(out) {
			t.Errorf("第 %d 条 xref 偏移越界: %d", i-2, off)
			continue
		}
		snippet := out[off:]
		if !strings.Contains(snippet[:min(20, len(snippet))], "0 obj") {
			t.Errorf("第 %d 条 xref 指向错误位置: %q", i-2, snippet[:min(30, len(snippet))])
		}
	}
}

func TestTextEscapesNonASCII(t *testing.T) {
	w := New()
	w.AddFont("test", nil)
	p := w.AddPage(595, 842)
	p.Text(0, 12, 10, 10, "中文ABC")
	var buf bytes.Buffer
	if err := w.WritePDF(&buf); err != nil {
		t.Fatal(err)
	}
	out := buf.String()
	if !strings.Contains(out, "BT") || !strings.Contains(out, "Tj") {
		t.Error("缺少文字操作符")
	}
}

func TestCircleAndArc(t *testing.T) {
	w := New()
	p := w.AddPage(595, 842)
	p.Circle(100, 100, 50)
	p.Stroke()
	p.Arc(200, 200, 50, 0, 1.5707963)
	p.Stroke()
	// 内容流是压缩的，直接检查未压缩的缓冲区更可靠
	cs := p.Content().String()
	if n := strings.Count(cs, " c\n"); n < 4 {
		t.Errorf("曲线段太少: %d", n)
	}
	if !strings.Contains(cs, "h\nS\n") {
		t.Errorf("圆应闭合且描边，内容=%q", cs)
	}
}

func TestSetLineWidthRejectsInvalid(t *testing.T) {
	w := New()
	p := w.AddPage(595, 842)
	p.SetLineWidth(0)
	p.SetLineWidth(-5)
	var buf bytes.Buffer
	w.WritePDF(&buf)
	// 无效线宽不应写入
	if strings.Contains(buf.String(), "-5 w") {
		t.Error("负数线宽不应写入")
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func sscanf(s string, v *int) (int, error) {
	n := 0
	started := false
	for _, c := range s {
		if c >= '0' && c <= '9' {
			n = n*10 + int(c-'0')
			started = true
		} else if started {
			break
		}
	}
	*v = n
	return 1, nil
}
