package render

import (
	"math"
	"runtime"

	"github.com/huanxiang666/dwg2pdf/internal/dxf"
	"github.com/huanxiang666/dwg2pdf/internal/pdf"
)

func runtimeNumCPU() int { return runtime.NumCPU() }

// drawCmd 是一条待写入的绘制指令。
type drawCmd struct {
	order int
	apply func(*pdf.Page)
}

// rgb 颜色。
type rgb struct{ r, g, b float64 }

// colorOf 返回实体颜色。
func (r *Renderer) colorOf(e *dxf.Entity) rgb {
	if r.opt.Monochrome {
		return rgb{0, 0, 0}
	}
	if e.Color < 0 {
		// ByLayer：查图层表拿真实颜色，仍拿不到就用黑色
		if e.LayerName != "" {
			if l, ok := r.d.Layers[e.LayerName]; ok && l.Color > 0 {
				return aciToRGB(int(l.Color))
			}
		}
		return rgb{0, 0, 0}
	}
	return aciToRGB(int(e.Color))
}

// aciToRGB 把 AutoCAD 颜色索引转 RGB。
func aciToRGB(aci int) rgb {
	if aci <= 0 {
		return rgb{0, 0, 0}
	}
	if aci > 255 {
		return rgb{
			float64((aci>>16)&0xFF) / 255,
			float64((aci>>8)&0xFF) / 255,
			float64(aci&0xFF) / 255,
		}
	}
	if aci == 7 {
		return rgb{0, 0, 0}
	}
	base := [7][3]float64{
		{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0},
		{0, 1, 1}, {0, 0, 1}, {1, 0, 1},
	}
	if aci < 7 {
		return rgb{base[aci][0], base[aci][1], base[aci][2]}
	}
	if aci == 8 {
		return rgb{0.5, 0.5, 0.5}
	}
	if aci == 9 {
		return rgb{0.75, 0.75, 0.75}
	}
	idx := (aci - 10) / 10
	hue := idx % 6
	shade := (aci - 10) % 10
	bright := 1.0 - float64(shade)*0.06
	if bright < 0.25 {
		bright = 0.25
	}
	hues := [6][3]float64{
		{1, 0, 0}, {1, 0.5, 0}, {1, 1, 0},
		{0, 1, 0}, {0, 1, 1}, {0, 0, 1},
	}
	if hue >= 0 && hue < 6 {
		return rgb{hues[hue][0] * bright, hues[hue][1] * bright, hues[hue][2] * bright}
	}
	return rgb{0, 0, 0}
}

// drawBatch 把一批实体转为绘制指令。
func (r *Renderer) drawBatch(ids []int, lineW float64) []drawCmd {
	cmds := make([]drawCmd, 0, len(ids))
	for _, id := range ids {
		e := &r.ents[id]
		cl := r.colorOf(e)

		switch e.Kind {
		case dxf.KindLine:
			if len(e.Pts) < 2 {
				continue
			}
			p0, p1 := e.Pts[0], e.Pts[1]
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.SetLineWidth(lineW)
				p.StrokeColor(cl.r, cl.g, cl.b)
				p.Line(p0.X, p0.Y, p1.X, p1.Y)
			}})

		case dxf.KindLwPolyline, dxf.KindPolyline:
			if len(e.Pts) < 2 {
				continue
			}
			pts := append([]dxf.Point(nil), e.Pts...)
			bulges := append([]float64(nil), e.Bulge...)
			closed := e.Closed
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.SetLineWidth(lineW)
				p.StrokeColor(cl.r, cl.g, cl.b)
				drawPolyline(p, pts, bulges, closed)
			}})

		case dxf.KindCircle:
			cx, cy := e.Center.X, e.Center.Y
			if len(e.Pts) > 0 {
				cx, cy = e.Pts[0].X, e.Pts[0].Y
			}
			rad := e.Radius
			if rad <= 0 {
				continue
			}
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.SetLineWidth(lineW)
				p.StrokeColor(cl.r, cl.g, cl.b)
				p.Circle(cx, cy, rad)
				p.Stroke()
			}})

		case dxf.KindArc:
			cx, cy := e.Center.X, e.Center.Y
			if len(e.Pts) > 0 {
				cx, cy = e.Pts[0].X, e.Pts[0].Y
			}
			rad, a0, a1 := e.Radius, e.StartA, e.EndA
			if rad <= 0 {
				continue
			}
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.SetLineWidth(lineW)
				p.StrokeColor(cl.r, cl.g, cl.b)
				p.Arc(cx, cy, rad, a0, a1)
				p.Stroke()
			}})

		case dxf.KindSolid:
			if len(e.Pts) < 3 {
				continue
			}
			pts := append([]dxf.Point(nil), e.Pts...)
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.FillColor(cl.r, cl.g, cl.b)
				p.SetLineWidth(lineW)
				p.StrokeColor(cl.r, cl.g, cl.b)
				drawPolylineFill(p, pts)
			}})

		case dxf.KindHatch:
			// HATCH 分两类：
			//   Solid=true  实心填充 —— 复合路径 + 奇偶规则填充
			//   Solid=false 图案填充（斜线、网格等）—— 只画边界，不填实心，
			//               否则整片区域会变成黑块，与原图严重不符。
			if len(e.Paths) == 0 {
				continue
			}
			paths := make([][]dxf.Point, len(e.Paths))
			for i := range e.Paths {
				paths[i] = append([]dxf.Point(nil), e.Paths[i]...)
			}
			solid := e.Solid
			patAngle := e.PatternAngle
			patSpacing := e.PatternSpacing
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.StrokeColor(cl.r, cl.g, cl.b)
				p.SetLineWidth(lineW * 0.35)
				if solid {
					p.FillColor(cl.r, cl.g, cl.b)
					drawHatchPaths(p, paths)
					return
				}
				// 图案填充：用斜线填充边界内部
				drawPatternHatch(p, paths, patAngle, patSpacing)
			}})

		case dxf.KindPoint:
			if len(e.Pts) < 1 {
				continue
			}
			px, py := e.Pts[0].X, e.Pts[0].Y
			// 点画成十字，尺寸按图纸单位估算
			sz := 20.0
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.StrokeColor(cl.r, cl.g, cl.b)
				p.SetLineWidth(lineW)
				p.MoveTo(px-sz, py)
				p.LineTo(px+sz, py)
				p.Stroke()
				p.MoveTo(px, py-sz)
				p.LineTo(px, py+sz)
				p.Stroke()
			}})

		case dxf.KindText:
			if e.Text == "" {
				continue
			}
			x, y := 0.0, 0.0
			if len(e.Pts) > 0 {
				x, y = e.Pts[0].X, e.Pts[0].Y
			}
			txt := e.Text
			h := e.Height
			if h <= 0 {
				h = 2.5
			}
			rot := e.Rotation
			fi := 0
			if r.fonts != nil {
				fi = r.fonts.FontIndex("")
			}
			cmds = append(cmds, drawCmd{order: id, apply: func(p *pdf.Page) {
				p.Save()
				p.FillColor(cl.r, cl.g, cl.b)
				if rot != 0 {
					cs, sn := math.Cos(rot), math.Sin(rot)
					p.Transform(cs, sn, -sn, cs, x, y)
					p.Text(fi, h, 0, 0, txt)
				} else {
					p.Text(fi, h, x, y, txt)
				}
				p.Restore()
			}})
		}
	}
	return cmds
}

// drawPatternHatch 用等距斜线填充边界内部。
// 实现：按角度生成一组平行线，与边界环求交得到线段。
func drawPatternHatch(p *pdf.Page, paths [][]dxf.Point, angle, spacing float64) {
	// 收集所有边界的线段
	type seg struct{ x1, y1, x2, y2 float64 }
	var segs []seg
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)

	for _, path := range paths {
		n := len(path)
		if n < 3 {
			continue
		}
		for i := 0; i < n; i++ {
			a := path[i]
			b := path[(i+1)%n]
			segs = append(segs, seg{a.X, a.Y, b.X, b.Y})
			for _, v := range []float64{a.X, b.X} {
				if v < minX {
					minX = v
				}
				if v > maxX {
					maxX = v
				}
			}
			for _, v := range []float64{a.Y, b.Y} {
				if v < minY {
					minY = v
				}
				if v > maxY {
					maxY = v
				}
			}
		}
	}
	if len(segs) == 0 || math.IsInf(minX, 1) {
		return
	}

	if spacing <= 0 {
		spacing = (maxY - minY) / 40
	}
	if spacing <= 0 || math.IsNaN(spacing) {
		return
	}

	ca, sa := math.Cos(angle), math.Sin(angle)
	// 沿法线方向平移生成平行线
	nx, ny := -sa, ca
	cx, cy := (minX+maxX)/2, (minY+maxY)/2
	// 覆盖范围半径
	rad := math.Hypot(maxX-minX, maxY-minY)/2 + spacing

	steps := int(rad/spacing) + 1
	if steps > 400 {
		steps = 400 // 防止极端情况爆量
	}

	for k := -steps; k <= steps; k++ {
		off := float64(k) * spacing
		// 直线上的点：中心 + 法线*off
		px, py := cx+nx*off, cy+ny*off
		// 沿方向向量延伸
		L := rad
		x1 := px - ca*L
		y1 := py - sa*L
		x2 := px + ca*L
		y2 := py + sa*L

		// 与边界求交，得到内部线段
		var ts []float64
		for _, s := range segs {
			t, ok := lineSegIntersect(x1, y1, x2, y2, s.x1, s.y1, s.x2, s.y2)
			if ok {
				ts = append(ts, t)
			}
		}
		if len(ts) < 2 {
			continue
		}
		sortFloats(ts)
		// 奇偶规则：成对连接
		for i := 0; i+1 < len(ts); i += 2 {
			t0, t1 := ts[i], ts[i+1]
			if t1-t0 < 1e-9 {
				continue
			}
			p.MoveTo(x1+(x2-x1)*t0, y1+(y2-y1)*t0)
			p.LineTo(x1+(x2-x1)*t1, y1+(y2-y1)*t1)
			p.Stroke()
		}
	}
}

// lineSegIntersect 求两条线段交点，返回第一条线段上的参数 t（0..1）。
func lineSegIntersect(x1, y1, x2, y2, x3, y3, x4, y4 float64) (float64, bool) {
	d := (x2-x1)*(y4-y3) - (y2-y1)*(x4-x3)
	if math.Abs(d) < 1e-12 {
		return 0, false
	}
	t := ((x3-x1)*(y4-y3) - (y3-y1)*(x4-x3)) / d
	u := ((x3-x1)*(y2-y1) - (y3-y1)*(x2-x1)) / d
	if t < 0 || t > 1 || u < 0 || u > 1 {
		return 0, false
	}
	return t, true
}

// drawRing 画一圈闭合边界（描边，不填充）。
func drawRing(p *pdf.Page, pts []dxf.Point) {
	clean := make([]dxf.Point, 0, len(pts))
	for _, pt := range pts {
		if pt.X == 0 && pt.Y == 0 {
			continue
		}
		clean = append(clean, pt)
	}
	if len(clean) < 2 {
		return
	}
	p.MoveTo(clean[0].X, clean[0].Y)
	for i := 1; i < len(clean); i++ {
		p.LineTo(clean[i].X, clean[i].Y)
	}
	p.ClosePath()
	p.Stroke()
}

// drawHatchPaths 把多条边界环合并为一个复合路径，用奇偶规则填充。
// 这样外环 + 内环（孔洞）能正确表现，不会填成实心黑块。
func drawHatchPaths(p *pdf.Page, paths [][]dxf.Point) {
	wrote := false
	for _, pts := range paths {
		clean := make([]dxf.Point, 0, len(pts))
		for _, pt := range pts {
			if pt.X == 0 && pt.Y == 0 {
				continue
			}
			clean = append(clean, pt)
		}
		if len(clean) < 3 {
			continue
		}
		p.MoveTo(clean[0].X, clean[0].Y)
		for i := 1; i < len(clean); i++ {
			p.LineTo(clean[i].X, clean[i].Y)
		}
		p.ClosePath()
		wrote = true
	}
	if wrote {
		p.FillStrokeEvenOdd()
	}
}

// drawPolylineFill 把点序列构成闭合多边形并填充。
func drawPolylineFill(p *pdf.Page, pts []dxf.Point) {
	// 过滤掉 (0,0)：部分 DXF 的 SOLID/HATCH 首点会被写成原点，
	// 会把填充拉成一条贯穿全图的黑块。
	clean := make([]dxf.Point, 0, len(pts))
	for _, pt := range pts {
		if pt.X == 0 && pt.Y == 0 {
			continue
		}
		clean = append(clean, pt)
	}
	if len(clean) < 3 {
		return
	}
	p.MoveTo(clean[0].X, clean[0].Y)
	for i := 1; i < len(clean); i++ {
		p.LineTo(clean[i].X, clean[i].Y)
	}
	p.ClosePath()
	p.FillStroke()
}

// drawPolylineFillBulge 支持圆弧边界的填充路径。
func drawPolylineFillBulge(p *pdf.Page, pts []dxf.Point, bulges []float64) {
	if len(pts) < 3 {
		return
	}
	p.MoveTo(pts[0].X, pts[0].Y)
	n := len(pts)
	for i := 0; i < n; i++ {
		a := pts[i]
		b := pts[(i+1)%n]
		var bulge float64
		if i < len(bulges) {
			bulge = bulges[i]
		}
		if math.Abs(bulge) < 1e-9 {
			p.LineTo(b.X, b.Y)
			continue
		}
		appendArcSeg(p, a, b, bulge)
	}
	p.ClosePath()
	p.FillStroke()
}

// appendArcSeg 在折线中追加一段 bulge 圆弧（不含起点 MoveTo）。
func appendArcSeg(p *pdf.Page, a, b dxf.Point, bulge float64) {
	theta := 4 * math.Atan(bulge)
	dx, dy := b.X-a.X, b.Y-a.Y
	chord := math.Hypot(dx, dy)
	if chord < 1e-12 {
		p.LineTo(b.X, b.Y)
		return
	}
	rad := chord / (2 * math.Sin(math.Abs(theta)/2))
	mx, my := (a.X+b.X)/2, (a.Y+b.Y)/2
	ux, uy := -dy/chord, dx/chord
	hh := math.Sqrt(math.Max(0, rad*rad-chord*chord/4))
	sign := 1.0
	if theta < 0 {
		sign = -1.0
	}
	cx := mx + ux*hh*sign
	cy := my + uy*hh*sign
	a0 := math.Atan2(a.Y-cy, a.X-cx)
	a1 := math.Atan2(b.Y-cy, b.X-cx)
	if theta > 0 {
		for a1 < a0 {
			a1 += 2 * math.Pi
		}
	} else {
		for a1 > a0 {
			a1 -= 2 * math.Pi
		}
	}
	arcTo(p, cx, cy, math.Abs(rad), a0, a1)
}

// drawPolyline 绘制折线，支持 bulge 圆弧段。
func drawPolyline(p *pdf.Page, pts []dxf.Point, bulges []float64, closed bool) {
	if len(pts) < 2 {
		return
	}
	p.MoveTo(pts[0].X, pts[0].Y)
	n := len(pts)
	limit := n - 1
	if closed {
		limit = n
	}
	for i := 0; i < limit; i++ {
		a := pts[i]
		b := pts[(i+1)%n]
		var bulge float64
		if i < len(bulges) {
			bulge = bulges[i]
		}
		if math.Abs(bulge) < 1e-9 {
			p.LineTo(b.X, b.Y)
			continue
		}
		theta := 4 * math.Atan(bulge)
		dx, dy := b.X-a.X, b.Y-a.Y
		chord := math.Hypot(dx, dy)
		if chord < 1e-12 {
			continue
		}
		rad := chord / (2 * math.Sin(math.Abs(theta)/2))
		mx, my := (a.X+b.X)/2, (a.Y+b.Y)/2
		ux, uy := -dy/chord, dx/chord
		hh := math.Sqrt(math.Max(0, rad*rad-chord*chord/4))
		sign := 1.0
		if theta < 0 {
			sign = -1.0
		}
		cx := mx + ux*hh*sign
		cy := my + uy*hh*sign
		a0 := math.Atan2(a.Y-cy, a.X-cx)
		a1 := math.Atan2(b.Y-cy, b.X-cx)
		if theta > 0 {
			for a1 < a0 {
				a1 += 2 * math.Pi
			}
		} else {
			for a1 > a0 {
				a1 -= 2 * math.Pi
			}
		}
		arcTo(p, cx, cy, math.Abs(rad), a0, a1)
	}
	if closed {
		p.ClosePath()
	}
	p.Stroke()
}

// arcTo 从当前点继续画弧。
func arcTo(p *pdf.Page, cx, cy, rad, a0, a1 float64) {
	delta := a1 - a0
	segs := int(math.Ceil(math.Abs(delta) / (math.Pi / 2)))
	if segs < 1 {
		segs = 1
	}
	step := delta / float64(segs)
	k := 4.0 / 3.0 * math.Tan(step/4)
	for i := 0; i < segs; i++ {
		cur := a0 + step*float64(i)
		nxt := cur + step
		x0, y0 := cx+rad*math.Cos(cur), cy+rad*math.Sin(cur)
		x1, y1 := cx+rad*math.Cos(nxt), cy+rad*math.Sin(nxt)
		c1x := x0 - k*rad*math.Sin(cur)
		c1y := y0 + k*rad*math.Cos(cur)
		c2x := x1 + k*rad*math.Sin(nxt)
		c2y := y1 - k*rad*math.Cos(nxt)
		p.CurveTo(c1x, c1y, c2x, c2y, x1, y1)
	}
}
