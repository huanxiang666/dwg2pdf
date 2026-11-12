package dxf

import "math"

// Flatten 把所有块引用展开为世界坐标下的实体列表。
//
// DXF 的 INSERT 语义：
//
//	世界点 = 插入点 + R(旋转) * S(缩放) * (块内点 - 块基点)
//
// 嵌套块递归展开，深度上限 12 层防止循环引用。
// 展开后的实体坐标全部是最终世界坐标，渲染器无需再做变换，
// 这既避免矩阵叠加错误，也便于并行绘制。
func (d *Drawing) Flatten() []Entity {
	out := make([]Entity, 0, len(d.Entities)*2)
	cache := make(map[string][]Entity, len(d.Blocks))

	for i := range d.Entities {
		e := &d.Entities[i]
		if e.Kind != KindInsert {
			out = append(out, *e)
			continue
		}
		blk, ok := d.Blocks[e.BlockName]
		if !ok || blk == nil {
			continue
		}
		// 先取块内已展开到"块局部坐标"的实体（缓存，避免重复计算）
		inner, ok := cache[e.BlockName]
		if !ok {
			inner = d.flattenBlock(blk, 0)
			cache[e.BlockName] = inner
		}
		m := d.insertMatrix(e, blk)
		for j := range inner {
			// 必须深拷贝：transformEntity 会就地改写 Pts/Center 等切片和字段，
			// 若直接复用缓存里的实体，会把缓存的坐标永久污染（并且反复相乘）。
			ne := inner[j]
			ne.Pts = append([]Point(nil), inner[j].Pts...)
			ne.Bulge = append([]float64(nil), inner[j].Bulge...)
			transformEntity(&ne, m)
			out = append(out, ne)
		}
	}
	return out
}

// insertMatrix 返回 INSERT 实体的变换矩阵。
//
// 关键发现（实测天正/国产 CAD 图纸）：块定义内的实体坐标很多已经是
// **世界坐标**，块基点常为 (0,0)。此时标准的 "平移到插入点" 语义会把
// 内容整体推飞十几倍。判断依据：块内容坐标量级远大于基点，且与插入点同量级。
//
// 对"绝对坐标块"，我们只保留旋转与缩放并以插入点为轴心，
// 保证内容仍落在原位附近；对普通块使用标准 DXF 语义。
func (d *Drawing) insertMatrix(e *Entity, blk *Block) Matrix {
	sx, sy := e.XScale, e.YScale
	if sx == 0 {
		sx = 1
	}
	if sy == 0 {
		sy = 1
	}
	ins := e.Pts0()

	// 缩放值健壮性：DXF 中 41/42 组码在部分国产 CAD 里被复用为比例尺
	//（实测出现 400/640/1000/1600 等值），这类值会让块内容被放大成天文数字。
	// 实践中块缩放很少超过 10 倍，超出范围时按 1 处理。
	if math.Abs(sx) > 10 || (math.Abs(sx) < 1e-3 && sx != 0) {
		sx = 1
	}
	if math.Abs(sy) > 10 || (math.Abs(sy) < 1e-3 && sy != 0) {
		sy = 1
	}

	if d.isAbsoluteBlock(blk) {
		// 绝对坐标块：内容已就位，只处理旋转/缩放（以插入点为轴心）
		if sx == 1 && sy == 1 && e.Rotation == 0 {
			return Identity()
		}
		m := Translate(-ins.X, -ins.Y)
		m = m.Mul(Scale(sx, sy))
		if e.Rotation != 0 {
			m = m.Mul(Rotate(e.Rotation))
		}
		m = m.Mul(Translate(ins.X, ins.Y))
		return m
	}

	m := Translate(-blk.BasePt.X, -blk.BasePt.Y)
	m = m.Mul(Scale(sx, sy))
	if e.Rotation != 0 {
		m = m.Mul(Rotate(e.Rotation))
	}
	m = m.Mul(Translate(ins.X, ins.Y))
	return m
}

// isAbsoluteBlock 判断块内实体是否已使用世界坐标。
// 依据：块内点离原点很远（>1e5），而块基点接近原点。
func (d *Drawing) isAbsoluteBlock(blk *Block) bool {
	if blk == nil || len(blk.Entities) == 0 {
		return false
	}
	if math.Abs(blk.BasePt.X) > 1e4 || math.Abs(blk.BasePt.Y) > 1e4 {
		return false
	}
	// 统计块内所有几何坐标的最大绝对值
	var maxAbs float64
	for i := range blk.Entities {
		e := &blk.Entities[i]
		for _, p := range e.Pts {
			if v := math.Abs(p.X); v > maxAbs {
				maxAbs = v
			}
			if v := math.Abs(p.Y); v > maxAbs {
				maxAbs = v
			}
		}
		// 圆/弧/文字可能把位置放在 Center 或 RotPr
		for _, p := range []Point{e.Center, e.RotPr} {
			if v := math.Abs(p.X); v > maxAbs {
				maxAbs = v
			}
			if v := math.Abs(p.Y); v > maxAbs {
				maxAbs = v
			}
		}
	}
	return maxAbs > 1e5
}

// flattenBlock 把块定义内的实体展开（含嵌套块），返回块局部坐标。
//
// 关键点：块内实体的坐标本身已是"块坐标"，不需要再应用自身基点变换。
// 只有嵌套的 INSERT 才需要把子块坐标映射到父块坐标。
// visited 用于检测循环引用（块 A 引用 B，B 又引用 A）。
func (d *Drawing) flattenBlock(blk *Block, depth int) []Entity {
	return d.flattenBlockRec(blk, depth, nil)
}

func (d *Drawing) flattenBlockRec(blk *Block, depth int, visited map[string]bool) []Entity {
	if depth > 10 || blk == nil {
		return nil
	}
	if visited == nil {
		visited = make(map[string]bool, 8)
	}
	if visited[blk.Name] {
		return nil // 循环引用
	}
	visited[blk.Name] = true
	defer delete(visited, blk.Name)

	out := make([]Entity, 0, len(blk.Entities))
	for i := range blk.Entities {
		e := &blk.Entities[i]
		if e.Kind != KindInsert {
			cp := *e
			cp.Pts = append([]Point(nil), e.Pts...)
			cp.Bulge = append([]float64(nil), e.Bulge...)
			if len(e.Paths) > 0 {
				cp.Paths = make([][]Point, len(e.Paths))
				for k := range e.Paths {
					cp.Paths[k] = append([]Point(nil), e.Paths[k]...)
				}
			}
			out = append(out, cp)
			continue
		}
		sub, ok := d.Blocks[e.BlockName]
		if !ok || sub == nil {
			continue
		}
		// 绝对坐标块：子块内容已就位，不做额外映射，直接并入
		if d.isAbsoluteBlock(sub) {
			out = append(out, d.flattenBlockRec(sub, depth+1, visited)...)
			continue
		}
		m := d.insertMatrix(e, sub)
		for _, inner := range d.flattenBlockRec(sub, depth+1, visited) {
			cp := inner
			cp.Pts = append([]Point(nil), inner.Pts...)
			cp.Bulge = append([]float64(nil), inner.Bulge...)
			if len(inner.Paths) > 0 {
				cp.Paths = make([][]Point, len(inner.Paths))
				for k := range inner.Paths {
					cp.Paths[k] = append([]Point(nil), inner.Paths[k]...)
				}
			}
			transformEntity(&cp, m)
			out = append(out, cp)
		}
	}
	return out
}

// transformEntity 对实体的几何数据应用矩阵（就地修改）。
func transformEntity(e *Entity, m Matrix) {
	// 先统一圆心与 Pts[0]（解析器可能只填了其中一个）
	if e.Kind == KindCircle || e.Kind == KindArc || e.Kind == KindEllipse {
		if len(e.Pts) > 0 {
			e.Center = e.Pts[0]
		} else if e.Center.X != 0 || e.Center.Y != 0 {
			e.Pts = []Point{e.Center}
		}
	}

	for i := range e.Pts {
		e.Pts[i] = m.Apply(e.Pts[i])
	}
	// HATCH 的每条边界路径都要变换
	for i := range e.Paths {
		for j := range e.Paths[i] {
			e.Paths[i][j] = m.Apply(e.Paths[i][j])
		}
	}

	if e.Kind == KindCircle || e.Kind == KindArc || e.Kind == KindEllipse {
		if len(e.Pts) > 0 {
			e.Center = e.Pts[0]
		} else {
			e.Center = m.Apply(e.Center)
		}
	}

	e.RotPr = m.Apply(e.RotPr)

	sx := math.Hypot(m.A, m.B)
	sy := math.Hypot(m.C, m.D)
	avg := (sx + sy) / 2
	if avg <= 0 {
		avg = 1
	}
	rot := math.Atan2(m.B, m.A)

	if e.Radius > 0 && e.Kind != KindText {
		e.Radius *= avg
	}
	if e.Kind == KindText && e.Height > 0 {
		e.Height *= avg
	}
	if e.Kind == KindText || e.Kind == KindArc {
		e.Rotation += rot
	}
	if e.Kind == KindArc {
		e.StartA += rot
		e.EndA += rot
	}
}
