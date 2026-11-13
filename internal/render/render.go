// Package render 把 DXF 图元渲染到 PDF 页面。
package render

import (
	"math"
	"sort"
	"sync"

	"github.com/huanxiang666/dwg2pdf/internal/dxf"
	"github.com/huanxiang666/dwg2pdf/internal/pdf"
)

// Options 渲染参数。
type Options struct {
	Monochrome bool
	LineWidth  float64 // 基础线宽（mm）
	Margin     float64 // 页边距（mm）
	PaperW     float64 // 目标纸宽（mm），0 表示自动
	PaperH     float64 // 目标纸高（mm），0 表示自动
	Scale      float64 // 附加缩放系数
	Workers    int     // 并行度，0 表示 CPU 核数
}

// DefaultOptions 返回默认参数。
func DefaultOptions() Options {
	return Options{LineWidth: 0.13, Margin: 0, Scale: 1, Workers: 0}
}

const mmToPt = 72.0 / 25.4

// renderer 渲染上下文。
type Renderer struct {
	d       *dxf.Drawing
	ents    []dxf.Entity // 展平到世界坐标后的实体
	opt     Options
	fonts   FontResolver
	w       *pdf.Writer
	lwScale float64
}

// FontResolver 把 CAD 字体名解析为 PDF 字体索引。
type FontResolver interface {
	FontIndex(name string) int
	Glyph(fontIdx int) func(r rune) uint16
}

// New 创建渲染器。
func New(d *dxf.Drawing, opt Options, fonts FontResolver) *Renderer {
	if opt.Scale == 0 {
		opt.Scale = 1
	}
	return &Renderer{d: d, ents: d.Flatten(), opt: opt, fonts: fonts}
}

// WithWriter 指定输出 writer（字体注册与页面写入必须同一实例）。
func (r *Renderer) WithWriter(w *pdf.Writer) *Renderer {
	r.w = w
	return r
}

// LineWidthScale 返回线宽补偿系数。
func (r *Renderer) LineWidthScale() float64 { return r.lwScale }

// Render 生成 PDF 并返回 writer。
func (r *Renderer) Render() *pdf.Writer {
	w := r.w
	if w == nil {
		w = pdf.New()
		r.w = w
	}

	if r.fonts != nil {
		r.fonts.FontIndex("")
	}

	minX, minY, maxX, maxY := r.bounds()
	if !(maxX > minX) || !(maxY > minY) {
		p := w.AddPage(210*mmToPt, 297*mmToPt)
		p.Save()
		p.Restore()
		return w
	}

	paperW, paperH := r.opt.PaperW, r.opt.PaperH
	if paperW <= 0 || paperH <= 0 {
		cw, ch := maxX-minX, maxY-minY
		const maxMM = 1189.0
		k := math.Min(maxMM/math.Max(cw, ch), 1000)
		paperW, paperH = cw*k, ch*k
		if paperW < 10 {
			paperW = 10
		}
		if paperH < 10 {
			paperH = 10
		}
		if paperW > maxMM {
			paperW = maxMM
		}
		if paperH > maxMM {
			paperH = maxMM
		}
	}

	margin := r.opt.Margin
	usableW := paperW - 2*margin
	usableH := paperH - 2*margin
	if usableW <= 0 || usableH <= 0 {
		usableW, usableH = paperW, paperH
		margin = 0
	}

	cw, ch := maxX-minX, maxY-minY
	s := math.Min(usableW/cw, usableH/ch) * r.opt.Scale

	drawW, drawH := cw*s, ch*s
	offXmm := margin + (usableW-drawW)/2
	offYmm := margin + (usableH-drawH)/2

	sPt := s * mmToPt
	offXpt := offXmm * mmToPt
	offYpt := offYmm * mmToPt
	r.lwScale = sPt

	page := w.AddPage(paperW*mmToPt, paperH*mmToPt)
	if r.fonts != nil {
		fr := r.fonts
		page.SetGlyphLookup(func(fontIdx int, ru rune) uint16 {
			fn := fr.Glyph(fontIdx)
			if fn == nil {
				return uint16(ru)
			}
			return fn(ru)
		})
	}
	page.Save()
	page.Transform(sPt, 0, 0, sPt, offXpt-sPt*minX, offYpt-sPt*minY)

	// 线宽：目标纸上约 0.18mm ≈ 0.51pt；变换后实际线宽 = lineW * sPt
	lineW := 0.51 / sPt
	if lineW < 0.8 {
		lineW = 0.8
	}

	buckets := r.partition(len(r.ents))
	results := make([][]drawCmd, len(buckets))

	var wg sync.WaitGroup
	for i, ids := range buckets {
		wg.Add(1)
		go func(bi int, idxs []int) {
			defer wg.Done()
			results[bi] = r.drawBatch(idxs, lineW)
		}(i, ids)
	}
	wg.Wait()

	var all []drawCmd
	for _, b := range results {
		all = append(all, b...)
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].order < all[j].order })
	for _, c := range all {
		c.apply(page)
	}

	page.Restore()
	return w
}

// bounds 计算内容边界。
// 优先级：HEADER 的 $EXTMIN/$EXTMAX > 实体坐标鲁棒统计。
// CAD 自己记录的图形范围最可靠，能避免个别异常实体把整张图拉伸。
func (r *Renderer) bounds() (minX, minY, maxX, maxY float64) {
	if r.d.HasHeaderExtents {
		minX, minY = r.d.HeaderMinX, r.d.HeaderMinY
		maxX, maxY = r.d.HeaderMaxX, r.d.HeaderMaxY
		if maxX > minX && maxY > minY {
			wx, wy := maxX-minX, maxY-minY
			// 留 2% 边距
			minX -= wx * 0.02
			maxX += wx * 0.02
			minY -= wy * 0.02
			maxY += wy * 0.02
			return
		}
	}

	xs := make([]float64, 0, len(r.ents)*4)
	ys := make([]float64, 0, len(r.ents)*4)

	collect := func(x, y float64) {
		if math.IsNaN(x) || math.IsNaN(y) || math.IsInf(x, 0) || math.IsInf(y, 0) {
			return
		}
		xs = append(xs, x)
		ys = append(ys, y)
	}

	for i := range r.ents {
		e := &r.ents[i]
		for _, p := range e.Pts {
			collect(p.X, p.Y)
		}
		if (e.Kind == dxf.KindCircle || e.Kind == dxf.KindArc) && e.Radius > 0 {
			collect(e.Center.X-e.Radius, e.Center.Y-e.Radius)
			collect(e.Center.X+e.Radius, e.Center.Y+e.Radius)
		}
	}
	if len(xs) == 0 {
		return
	}

	mx, my := median(xs), median(ys)
	dx, dy := mad(xs, mx), mad(ys, my)

	kx, ky := math.Inf(1), math.Inf(1)
	if dx > 0 {
		kx = 20 * dx
	}
	if dy > 0 {
		ky = 20 * dy
	}

	minX, minY = math.Inf(1), math.Inf(1)
	maxX, maxY = math.Inf(-1), math.Inf(-1)
	cnt := 0
	for i := range xs {
		if math.Abs(xs[i]-mx) > kx || math.Abs(ys[i]-my) > ky {
			continue
		}
		if xs[i] < minX {
			minX = xs[i]
		}
		if xs[i] > maxX {
			maxX = xs[i]
		}
		if ys[i] < minY {
			minY = ys[i]
		}
		if ys[i] > maxY {
			maxY = ys[i]
		}
		cnt++
	}

	// 过滤过狠时退回分位数
	if cnt < len(xs)/10 || !(maxX > minX) || !(maxY > minY) {
		minX, maxX = quantile(xs, 0.02), quantile(xs, 0.98)
		minY, maxY = quantile(ys, 0.02), quantile(ys, 0.98)
	}

	wx, wy := maxX-minX, maxY-minY
	if wx <= 0 {
		wx = 1
	}
	if wy <= 0 {
		wy = 1
	}
	minX -= wx * 0.02
	maxX += wx * 0.02
	minY -= wy * 0.02
	maxY += wy * 0.02
	return
}

func median(v []float64) float64 {
	if len(v) == 0 {
		return 0
	}
	cp := make([]float64, len(v))
	copy(cp, v)
	sortFloats(cp)
	return cp[len(cp)/2]
}

func mad(v []float64, med float64) float64 {
	if len(v) == 0 {
		return 0
	}
	dev := make([]float64, len(v))
	for i, x := range v {
		dev[i] = math.Abs(x - med)
	}
	sortFloats(dev)
	return dev[len(dev)/2]
}

func quantile(v []float64, q float64) float64 {
	if len(v) == 0 {
		return 0
	}
	cp := make([]float64, len(v))
	copy(cp, v)
	sortFloats(cp)
	idx := int(q * float64(len(cp)-1))
	if idx < 0 {
		idx = 0
	}
	if idx >= len(cp) {
		idx = len(cp) - 1
	}
	return cp[idx]
}

func sortFloats(v []float64) { quickSort(v, 0, len(v)-1) }

func quickSort(a []float64, lo, hi int) {
	for lo < hi {
		if hi-lo < 12 {
			for i := lo + 1; i <= hi; i++ {
				for j := i; j > lo && a[j] < a[j-1]; j-- {
					a[j], a[j-1] = a[j-1], a[j]
				}
			}
			return
		}
		mid := (lo + hi) / 2
		if a[mid] < a[lo] {
			a[mid], a[lo] = a[lo], a[mid]
		}
		if a[hi] < a[lo] {
			a[hi], a[lo] = a[lo], a[hi]
		}
		if a[hi] < a[mid] {
			a[hi], a[mid] = a[mid], a[hi]
		}
		pivot := a[mid]
		i, j := lo, hi
		for i <= j {
			for a[i] < pivot {
				i++
			}
			for a[j] > pivot {
				j--
			}
			if i <= j {
				a[i], a[j] = a[j], a[i]
				i++
				j--
			}
		}
		if j-lo < hi-i {
			quickSort(a, lo, j)
			lo = i
		} else {
			quickSort(a, i, hi)
			hi = j
		}
	}
}

// partition 把实体索引切成若干桶，尽量均衡。
func (r *Renderer) partition(n int) [][]int {
	workers := r.opt.Workers
	if workers <= 0 {
		workers = runtimeNumCPU()
	}
	if workers > n {
		workers = n
	}
	if workers < 1 {
		workers = 1
	}
	buckets := make([][]int, workers)
	for i := 0; i < n; i++ {
		buckets[i%workers] = append(buckets[i%workers], i)
	}
	return buckets
}
