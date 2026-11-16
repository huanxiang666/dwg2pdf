// Package preview 把展平后的图纸实体转成 SVG，供图形界面做
// 缩放/平移预览与框选导出（屏幕坐标 ↔ 图纸坐标可精确换算）。
//
// 性能取向：同色描边合并为单个 <path>，弧/椭圆降采样，文本限量，
// 使 10 万图元级的图纸 SVG 保持在数 MB 内（再经 gzip 传输）。
package preview

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/huanxiang666/dwg2pdf/internal/dxf"
)

// Result 是预览产物。
type Result struct {
	SVG         string  `json:"svg"`
	MinX        float64 `json:"minX"`
	MinY        float64 `json:"minY"`
	MaxX        float64 `json:"maxX"`
	MaxY        float64 `json:"maxY"`
	EntityCount int     `json:"entityCount"`
}

// aciColor 把 AutoCAD 颜色索引近似为 RGB（常用色，其余回退深灰）。
func aciColor(i int, mono bool) string {
	if mono {
		return "#111111"
	}
	switch i {
	case 1:
		return "#cc0000"
	case 2:
		return "#cccc00"
	case 3:
		return "#00aa00"
	case 4:
		return "#00cccc"
	case 5:
		return "#0044cc"
	case 6:
		return "#aa00aa"
	case 7:
		return "#222222"
	case 8:
		return "#888888"
	case 9:
		return "#bbbbbb"
	case 30:
		return "#ff8800"
	case 40:
		return "#ffaa00"
	case 60:
		return "#88aa00"
	case 90:
		return "#775533"
	case 250:
		return "#333333"
	}
	if i <= 0 || i > 255 {
		return "#222222"
	}
	return "#444444"
}

const (
	arcSegs     = 16
	ellipseSegs = 24
	maxTexts    = 15000
)

type textEl struct {
	x, y, size float64
	col, txt   string
}

// Build 生成 SVG。mono 为真时全部画成深色。
func Build(ents []dxf.Entity, mono bool) Result {
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)
	collect := func(x, y float64) {
		if math.Abs(x) > 1e9 || math.Abs(y) > 1e9 {
			return
		}
		minX = math.Min(minX, x)
		maxX = math.Max(maxX, x)
		minY = math.Min(minY, y)
		maxY = math.Max(maxY, y)
	}
	nText := 0
	for i := range ents {
		e := &ents[i]
		for _, p := range e.Pts {
			collect(p.X, p.Y)
		}
		for _, p := range e.Fit {
			collect(p.X, p.Y)
		}
		if e.Kind == dxf.KindCircle || e.Kind == dxf.KindArc {
			collect(e.Center.X-e.Radius, e.Center.Y-e.Radius)
			collect(e.Center.X+e.Radius, e.Center.Y+e.Radius)
		}
		if e.Kind == dxf.KindEllipse {
			m := math.Hypot(e.RotPr.X, e.RotPr.Y) * (1 + e.EllipseRatio)
			collect(e.Center.X-m, e.Center.Y-m)
			collect(e.Center.X+m, e.Center.Y+m)
		}
		for _, path := range e.Paths {
			for _, p := range path {
				collect(p.X, p.Y)
			}
		}
		if e.Kind == dxf.KindText && e.Text != "" && len(e.Pts) > 0 {
			nText++
		}
	}
	if math.IsInf(minX, 1) {
		minX, minY, maxX, maxY = 0, 0, 100, 100
	}
	w, h := maxX-minX, maxY-minY
	if w <= 0 {
		w = 1
	}
	if h <= 0 {
		h = 1
	}
	strokeW := w / 1500

	strokes := map[string]*strings.Builder{}
	fills := map[string]*strings.Builder{}
	stroke := func(col string) *strings.Builder {
		b, ok := strokes[col]
		if !ok {
			b = &strings.Builder{}
			strokes[col] = b
		}
		return b
	}
	fill := func(col string) *strings.Builder {
		b, ok := fills[col]
		if !ok {
			b = &strings.Builder{}
			fills[col] = b
		}
		return b
	}
	polyline := func(b *strings.Builder, pts []dxf.Point, closed bool) {
		if len(pts) < 2 {
			return
		}
		fmt.Fprintf(b, "M%.2f %.2f", pts[0].X, pts[0].Y)
		for _, p := range pts[1:] {
			fmt.Fprintf(b, "L%.2f %.2f", p.X, p.Y)
		}
		if closed {
			b.WriteString("Z")
		}
	}

	var texts []textEl
	textCap := maxTexts
	if nText < textCap {
		textCap = nText
	}

	for i := range ents {
		e := &ents[i]
		col := aciColor(int(e.Color), mono)
		switch e.Kind {
		case dxf.KindLine:
			if len(e.Pts) >= 2 {
				polyline(stroke(col), e.Pts[:2], false)
			}
		case dxf.KindLwPolyline, dxf.KindPolyline:
			polyline(stroke(col), e.Pts, e.Closed)
		case dxf.KindCircle:
			b := stroke(col)
			fmt.Fprintf(b, "M%.2f %.2fA%.2f %.2f 0 1 0 %.2f %.2fA%.2f %.2f 0 1 0 %.2f %.2f",
				e.Center.X-e.Radius, e.Center.Y, e.Radius, e.Radius, e.Center.X+e.Radius, e.Center.Y,
				e.Radius, e.Radius, e.Center.X-e.Radius, e.Center.Y)
		case dxf.KindArc:
			a0, a1 := e.StartA, e.EndA
			if a1 <= a0 {
				a1 += 2 * math.Pi
			}
			b := stroke(col)
			for s := 0; s <= arcSegs; s++ {
				t := a0 + (a1-a0)*float64(s)/float64(arcSegs)
				x := e.Center.X + e.Radius*math.Cos(t)
				y := e.Center.Y + e.Radius*math.Sin(t)
				if s == 0 {
					fmt.Fprintf(b, "M%.2f %.2f", x, y)
				} else {
					fmt.Fprintf(b, "L%.2f %.2f", x, y)
				}
			}
		case dxf.KindEllipse:
			mx, my := e.RotPr.X, e.RotPr.Y
			ratio := e.EllipseRatio
			if ratio <= 0 {
				ratio = 1
			}
			nx, ny := -my*ratio, mx*ratio
			t0, t1 := e.StartP, e.EndP
			if t1 <= t0 {
				t1 = t0 + 2*math.Pi
			}
			b := stroke(col)
			for s := 0; s <= ellipseSegs; s++ {
				t := t0 + (t1-t0)*float64(s)/float64(ellipseSegs)
				x := e.Center.X + mx*math.Cos(t) + nx*math.Sin(t)
				y := e.Center.Y + my*math.Cos(t) + ny*math.Sin(t)
				if s == 0 {
					fmt.Fprintf(b, "M%.2f %.2f", x, y)
				} else {
					fmt.Fprintf(b, "L%.2f %.2f", x, y)
				}
			}
		case dxf.KindSpline:
			pts := e.Fit
			if len(pts) < 2 {
				pts = e.Pts
			}
			polyline(stroke(col), pts, false)
		case dxf.KindSolid:
			polyline(fill(col), e.Pts, true)
		case dxf.KindHatch:
			for _, path := range e.Paths {
				polyline(stroke(col), path, true)
			}
		case dxf.KindText:
			if len(texts) < textCap && len(e.Pts) > 0 && e.Text != "" {
				size := e.Height
				if size <= 0 {
					size = h / 60
				}
				txt := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;").Replace(e.Text)
				txt = strings.ReplaceAll(txt, "\n", " ")
				texts = append(texts, textEl{e.Pts[0].X, e.Pts[0].Y, size, col, txt})
			}
		}
	}

	var b strings.Builder
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="%.3f %.3f %.3f %.3f" preserveAspectRatio="xMidYMid meet"><g transform="scale(1,-1)">`, minX, -maxY, w, h)
	cols := make([]string, 0, len(strokes))
	for c := range strokes {
		cols = append(cols, c)
	}
	sort.Strings(cols)
	for _, c := range cols {
		fmt.Fprintf(&b, `<path d="%s" fill="none" stroke="%s" stroke-width="%.4f"/>`, strokes[c].String(), c, strokeW)
	}
	fcols := make([]string, 0, len(fills))
	for c := range fills {
		fcols = append(fcols, c)
	}
	sort.Strings(fcols)
	for _, c := range fcols {
		fmt.Fprintf(&b, `<path d="%s" fill="%s" fill-opacity="0.5" stroke="none"/>`, fills[c].String(), c)
	}
	b.WriteString(`</g>`)
	for _, t := range texts {
		fmt.Fprintf(&b, `<text x="%.2f" y="%.2f" font-size="%.2f" fill="%s" font-family="sans-serif">%s</text>`,
			t.x, -t.y, t.size, t.col, t.txt)
	}
	b.WriteString(`</svg>`)
	return Result{SVG: b.String(), MinX: minX, MinY: minY, MaxX: maxX, MaxY: maxY, EntityCount: len(ents)}
}
