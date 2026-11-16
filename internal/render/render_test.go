package render

import (
	"math"
	"testing"

	"github.com/huanxiang666/dwg2pdf/internal/dxf"
)

// fakeFonts 是测试用的字体解析桩。
type fakeFonts struct{}

func (fakeFonts) FontIndex(string) int          { return 0 }
func (fakeFonts) Glyph(int) func(r rune) uint16 { return nil }

func mkLine(x1, y1, x2, y2 float64) dxf.Entity {
	return dxf.Entity{Kind: dxf.KindLine, Pts: []dxf.Point{{X: x1, Y: y1}, {X: x2, Y: y2}}}
}

func TestBoundsPrefersHeaderExtents(t *testing.T) {
	d := &dxf.Drawing{
		Entities:         []dxf.Entity{mkLine(0, 0, 100, 100)},
		HeaderMinX:       -50,
		HeaderMinY:       -50,
		HeaderMaxX:       150,
		HeaderMaxY:       150,
		HasHeaderExtents: true,
	}
	r := New(d, DefaultOptions(), fakeFonts{})
	minX, minY, maxX, maxY := r.bounds()
	if minX >= 0 || minY >= 0 || maxX <= 100 || maxY <= 100 {
		t.Errorf("应使用 HEADER 范围，得到 (%.1f,%.1f)-(%.1f,%.1f)", minX, minY, maxX, maxY)
	}
}

func TestBoundsIgnoresOutliers(t *testing.T) {
	// 大量正常点 + 一个天文数字离群点
	ents := make([]dxf.Entity, 0, 200)
	for i := 0; i < 200; i++ {
		x := float64(i)
		ents = append(ents, mkLine(x, x, x+1, x+1))
	}
	ents = append(ents, mkLine(0, 0, 1e12, 1e12))

	d := &dxf.Drawing{Entities: ents}
	r := New(d, DefaultOptions(), fakeFonts{})
	_, _, maxX, _ := r.bounds()
	if maxX > 1e6 {
		t.Errorf("离群点未被过滤，maxX=%.0f", maxX)
	}
}

func TestRenderProducesPage(t *testing.T) {
	d := &dxf.Drawing{
		Entities: []dxf.Entity{
			mkLine(0, 0, 100, 0),
			mkLine(100, 0, 100, 100),
			mkLine(100, 100, 0, 100),
			mkLine(0, 100, 0, 0),
			{Kind: dxf.KindCircle, Center: dxf.Point{X: 50, Y: 50}, Radius: 20,
				Pts: []dxf.Point{{X: 50, Y: 50}}},
		},
		Blocks: map[string]*dxf.Block{},
		Layers: map[string]dxf.Layer{},
	}
	opt := DefaultOptions()
	opt.PaperW, opt.PaperH = 210, 297
	r := New(d, opt, fakeFonts{})
	w := r.Render()
	if w == nil {
		t.Fatal("Render 返回 nil")
	}
}

func TestEmptyDrawingNoPanic(t *testing.T) {
	d := &dxf.Drawing{Blocks: map[string]*dxf.Block{}, Layers: map[string]dxf.Layer{}}
	r := New(d, DefaultOptions(), fakeFonts{})
	if w := r.Render(); w == nil {
		t.Fatal("空图纸应输出空白页而不是 nil")
	}
}

func TestACIColors(t *testing.T) {
	cases := []struct {
		aci int
		r   float64
	}{
		{1, 1}, // 红
		{3, 0}, // 绿 (g=1, r=0)
		{5, 0}, // 蓝
	}
	for _, c := range cases {
		got := aciToRGB(c.aci)
		if math.Abs(got.r-c.r) > 1e-6 && c.aci == 1 {
			t.Errorf("aci %d 红色分量错误: %v", c.aci, got)
		}
	}
	// ACI 7 在白纸上必须是黑色
	if got := aciToRGB(7); got.r != 0 || got.g != 0 || got.b != 0 {
		t.Errorf("ACI 7 应为黑色，得到 %v", got)
	}
	// 任意颜色都不应为 NaN
	for aci := 0; aci <= 255; aci++ {
		got := aciToRGB(aci)
		if math.IsNaN(got.r) || math.IsNaN(got.g) || math.IsNaN(got.b) {
			t.Errorf("aci %d 产生 NaN", aci)
		}
	}
}

func TestMonochromeForcesBlack(t *testing.T) {
	d := &dxf.Drawing{Entities: []dxf.Entity{{Kind: dxf.KindLine, Color: 1}}}
	opt := DefaultOptions()
	opt.Monochrome = true
	r := New(d, opt, fakeFonts{})
	got := r.colorOf(&d.Entities[0])
	if got.r != 0 || got.g != 0 || got.b != 0 {
		t.Errorf("单色模式应为黑色，得到 %v", got)
	}
}

func TestByLayerUsesLayerColor(t *testing.T) {
	d := &dxf.Drawing{
		Entities: []dxf.Entity{{Kind: dxf.KindLine, Color: -1, LayerName: "WALLS"}},
		Layers:   map[string]dxf.Layer{"WALLS": {Name: "WALLS", Color: 1}},
	}
	r := New(d, DefaultOptions(), fakeFonts{})
	got := r.colorOf(&d.Entities[0])
	if math.Abs(got.r-1) > 1e-6 || got.g != 0 {
		t.Errorf("ByLayer 应取图层红色，得到 %v", got)
	}
}
