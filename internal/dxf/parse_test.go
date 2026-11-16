package dxf

import (
	"math"
	"testing"
)

// buildDXF 用组码/值对拼出最小 DXF 内容。
func buildDXF(pairs [][2]string) []byte {
	var b []byte
	for _, p := range pairs {
		b = append(b, p[0]...)
		b = append(b, '\n')
		b = append(b, p[1]...)
		b = append(b, '\n')
	}
	return b
}

func TestParseLine(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "LINE"}, {"8", "0"}, {"10", "1.0"}, {"20", "2.0"}, {"11", "3.0"}, {"21", "4.0"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, err := Parse(buf)
	if err != nil {
		t.Fatal(err)
	}
	if len(d.Entities) != 1 {
		t.Fatalf("期望 1 个实体，得到 %d", len(d.Entities))
	}
	e := d.Entities[0]
	if e.Kind != KindLine {
		t.Errorf("类型错误: %v", e.Kind)
	}
	if len(e.Pts) != 2 {
		t.Fatalf("期望 2 个点，得到 %d", len(e.Pts))
	}
	if e.Pts[0] != (Point{1, 2}) || e.Pts[1] != (Point{3, 4}) {
		t.Errorf("坐标错误: %v %v", e.Pts[0], e.Pts[1])
	}
}

func TestParseCircleSyncsCenter(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "CIRCLE"}, {"8", "0"}, {"10", "5.0"}, {"20", "6.0"}, {"40", "2.5"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	if len(d.Entities) != 1 {
		t.Fatalf("期望 1 个实体，得到 %d", len(d.Entities))
	}
	e := d.Entities[0]
	if e.Radius != 2.5 {
		t.Errorf("半径错误: %v", e.Radius)
	}
	// Center 必须与 Pts[0] 同步，否则变换时会漂移
	if e.Center.X != 5 || e.Center.Y != 6 {
		t.Errorf("圆心未同步: %v", e.Center)
	}
}

func TestParseArcAngles(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "ARC"}, {"10", "0"}, {"20", "0"}, {"40", "10"},
		{"50", "0"}, {"51", "90"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	e := d.Entities[0]
	if math.Abs(e.StartA) > 1e-9 {
		t.Errorf("起始角错误: %v", e.StartA)
	}
	if math.Abs(e.EndA-math.Pi/2) > 1e-9 {
		t.Errorf("终止角应为 π/2，得到 %v", e.EndA)
	}
}

func TestParseText(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "TEXT"}, {"8", "0"}, {"10", "100"}, {"20", "200"}, {"40", "3.5"}, {"1", "中文测试"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	if len(d.Entities) != 1 {
		t.Fatalf("期望 1 个实体")
	}
	e := d.Entities[0]
	if e.Text != "中文测试" {
		t.Errorf("文本错误: %q", e.Text)
	}
	if e.Height != 3.5 {
		t.Errorf("字高错误: %v", e.Height)
	}
}

func TestParseHeaderExtents(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "HEADER"},
		{"9", "$EXTMIN"}, {"10", "-100"}, {"20", "-200"}, {"30", "0"},
		{"9", "$EXTMAX"}, {"10", "100"}, {"20", "200"}, {"30", "0"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	if !d.HasHeaderExtents {
		t.Fatal("应识别到 HEADER 范围")
	}
	if d.HeaderMinX != -100 || d.HeaderMinY != -200 {
		t.Errorf("EXTMIN 错误: %v %v", d.HeaderMinX, d.HeaderMinY)
	}
	if d.HeaderMaxX != 100 || d.HeaderMaxY != 200 {
		t.Errorf("EXTMAX 错误: %v %v", d.HeaderMaxX, d.HeaderMaxY)
	}
}

func TestParseLayerColor(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "TABLES"},
		{"0", "LAYER"}, {"2", "WALLS"}, {"62", "1"},
		{"0", "ENDTAB"},
		{"0", "ENDSEC"},
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "LINE"}, {"8", "WALLS"}, {"10", "0"}, {"20", "0"}, {"11", "1"}, {"21", "1"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	l, ok := d.Layers["WALLS"]
	if !ok {
		t.Fatal("未解析到 WALLS 图层")
	}
	if l.Color != 1 {
		t.Errorf("图层颜色应为 1，得到 %d", l.Color)
	}
	if d.Entities[0].LayerName != "WALLS" {
		t.Errorf("实体的图层名错误: %q", d.Entities[0].LayerName)
	}
}

func TestParseHatchPaths(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "HATCH"}, {"8", "0"}, {"70", "1"},
		{"91", "1"}, {"92", "1"}, {"93", "4"}, {"97", "0"},
		{"10", "0"}, {"20", "0"},
		{"10", "10"}, {"20", "0"},
		{"10", "10"}, {"20", "10"},
		{"10", "0"}, {"20", "10"},
		{"97", "0"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	if len(d.Entities) != 1 {
		t.Fatalf("期望 1 个 HATCH，得到 %d", len(d.Entities))
	}
	e := d.Entities[0]
	if e.Kind != KindHatch {
		t.Fatalf("类型错误: %v", e.Kind)
	}
	if !e.Solid {
		t.Error("应识别为实心填充")
	}
	if len(e.Paths) != 1 {
		t.Fatalf("期望 1 条边界路径，得到 %d", len(e.Paths))
	}
	if len(e.Paths[0]) != 4 {
		t.Errorf("期望 4 个顶点，得到 %d", len(e.Paths[0]))
	}
}

func TestParseBulge(t *testing.T) {
	buf := buildDXF([][2]string{
		{"0", "SECTION"}, {"2", "ENTITIES"},
		{"0", "LWPOLYLINE"}, {"70", "1"},
		{"10", "0"}, {"20", "0"}, {"42", "0.5"},
		{"10", "10"}, {"20", "0"},
		{"0", "ENDSEC"}, {"0", "EOF"},
	})
	d, _ := Parse(buf)
	e := d.Entities[0]
	if !e.Closed {
		t.Error("应识别为闭合")
	}
	if len(e.Bulge) < 2 || e.Bulge[0] != 0.5 {
		t.Errorf("凸度错误: %v", e.Bulge)
	}
}

func TestScannerSkipsBOM(t *testing.T) {
	raw := append([]byte{0xEF, 0xBB, 0xBF}, []byte("0\nSECTION\n0\nEOF\n")...)
	s := NewScanner(raw)
	code, val, ok := s.Next()
	if !ok || code != 0 || string(val) != "SECTION" {
		t.Fatalf("BOM 处理失败: code=%d val=%q ok=%v", code, val, ok)
	}
}

func TestScannerTolerantToLeadingSpace(t *testing.T) {
	s := NewScanner([]byte("  0\nSECTION\n  20\n  -5.5\n"))
	code, _, ok := s.Next()
	if !ok || code != 0 {
		t.Fatalf("应容忍前导空格: code=%d", code)
	}
	_, val, ok := s.Next()
	if !ok || Float(val) != -5.5 {
		t.Fatalf("值解析错误: %q", val)
	}
}
