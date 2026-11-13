// Package dxf：实体模型定义。
package dxf

// Point 二维点。
type Point struct{ X, Y float64 }

// Matrix 2D 仿射变换矩阵 [a b c d e f]，等价 PDF 的 cm 操作符。
// x' = a*x + c*y + e
// y' = b*x + d*y + f
type Matrix struct {
	A, B, C, D, E, F float64
}

// Identity 单位矩阵。
func Identity() Matrix { return Matrix{A: 1, D: 1} }

// Mul 返回 m * n（先应用 n，再应用 m）。
func (m Matrix) Mul(n Matrix) Matrix {
	return Matrix{
		A: m.A*n.A + m.C*n.B,
		B: m.B*n.A + m.D*n.B,
		C: m.A*n.C + m.C*n.D,
		D: m.B*n.C + m.D*n.D,
		E: m.A*n.E + m.C*n.F + m.E,
		F: m.B*n.E + m.D*n.F + m.F,
	}
}

// Apply 变换点。
func (m Matrix) Apply(p Point) Point {
	return Point{X: m.A*p.X + m.C*p.Y + m.E, Y: m.B*p.X + m.D*p.Y + m.F}
}

// Translate 平移。
func Translate(dx, dy float64) Matrix { return Matrix{A: 1, D: 1, E: dx, F: dy} }

// Scale 缩放。
func Scale(sx, sy float64) Matrix { return Matrix{A: sx, D: sy} }

// Rotate 旋转（弧度）。
func Rotate(rad float64) Matrix {
	c, s := cos(rad), sin(rad)
	return Matrix{A: c, B: s, C: -s, D: c}
}

// EntityKind 实体类型。
type EntityKind uint8

const (
	KindLine EntityKind = iota
	KindLwPolyline
	KindCircle
	KindArc
	KindEllipse
	KindText
	KindInsert
	KindSolid
	KindPoint
	KindPolyline
	KindHatch
)

// Entity 通用实体。
type Entity struct {
	Kind  EntityKind
	Layer int
	Color int32 // -1 表示 ByLayer
	// LayerName 图层名，ByLayer 时据此查表取色
	LayerName string
	Pts       []Point
	Bulge     []float64
	Closed    bool
	// HATCH 的多条边界路径（每条是一圈闭合多边形）
	Paths [][]Point
	// Solid 标记实心填充（HATCH 组码 70 == 1）。
	// 图案填充（0）需要用线图案填充，否则会变成黑块。
	Solid bool
	// PatternAngle/PatternSpacing 描述图案填充的线角度与间距（弧度/图纸单位）。
	PatternAngle   float64
	PatternSpacing float64
	// 圆/弧/椭圆
	Center Point
	Radius float64
	StartA float64
	EndA   float64
	MajorA float64 // 椭圆长轴
	// 文本
	Text   string
	Height float64
	RotPr  Point // 旋转/对齐点
	Width  float64
	// 块引用
	BlockName string
	XScale    float64
	YScale    float64
	Rotation  float64
	Matrix    Matrix // 世界变换（解析时累积）
	Width2    float64
}

func cos(x float64) float64 { return cosImpl(x) }
func sin(x float64) float64 { return sinImpl(x) }

// Pts0 返回第一个点（安全）。
func (e *Entity) Pts0() Point {
	if len(e.Pts) > 0 {
		return e.Pts[0]
	}
	return Point{}
}

// Block 块定义。
type Block struct {
	Name     string
	Entities []Entity
	BasePt   Point
}

// Drawing 解析结果。
type Drawing struct {
	Entities []Entity
	Blocks   map[string]*Block
	Layers   map[string]Layer
	// 内容边界
	MinX, MinY, MaxX, MaxY float64
	HasExtents             bool
	// HEADER 中记录的图形范围（$EXTMIN/$EXTMAX），最可靠
	HeaderMinX, HeaderMinY, HeaderMaxX, HeaderMaxY float64
	HasHeaderExtents                               bool
}

// Layer 图层信息。
type Layer struct {
	Name  string
	Color int32
}

// AddExtents 扩展边界。
func (d *Drawing) AddExtents(x, y float64) {
	if x != x || y != y { // NaN
		return
	}
	if !d.HasExtents {
		d.MinX, d.MaxX = x, x
		d.MinY, d.MaxY = y, y
		d.HasExtents = true
		return
	}
	if x < d.MinX {
		d.MinX = x
	}
	if x > d.MaxX {
		d.MaxX = x
	}
	if y < d.MinY {
		d.MinY = y
	}
	if y > d.MaxY {
		d.MaxY = y
	}
}
