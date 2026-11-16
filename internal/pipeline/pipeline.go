// Package pipeline 把"输入图纸 → 输出 PDF"的完整流程封装成一个函数，
// 供命令行与图形界面（Web UI）共用，保证两种入口行为完全一致。
package pipeline

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/huanxiang666/dwg2pdf/internal/dwg"
	"github.com/huanxiang666/dwg2pdf/internal/dxf"
	"github.com/huanxiang666/dwg2pdf/internal/font"
	"github.com/huanxiang666/dwg2pdf/internal/pdf"
	"github.com/huanxiang666/dwg2pdf/internal/render"
)

// Options 是转换参数。
type Options struct {
	Monochrome   bool
	LineWidth    float64 // mm
	Margin       float64 // mm
	PaperW       float64 // mm，0 表示自动
	PaperH       float64
	Scale        float64
	SkipExisting bool
	// Engine 首选 DWG 引擎："auto" / "libredwg" / "oda"
	Engine string
	// Quality 质量档："draft" / "balanced" / "high"
	Quality string
	// Clip 框选导出区域（图纸坐标），HasClip 为真时只输出该矩形
	HasClip            bool
	ClipMinX, ClipMinY float64
	ClipMaxX, ClipMaxY float64
}

// DefaultOptions 返回默认参数。
func DefaultOptions() Options {
	return Options{LineWidth: 0.13, Margin: 0, Scale: 1, Engine: "auto", Quality: "balanced"}
}

// applyQuality 把质量档映射为线宽与细节开关。
func (o *Options) applyQuality() {
	switch o.Quality {
	case "draft":
		if o.LineWidth <= 0 || o.LineWidth == 0.13 {
			o.LineWidth = 0.20
		}
	case "high":
		if o.LineWidth <= 0 || o.LineWidth == 0.13 {
			o.LineWidth = 0.09
		}
	default: // balanced
		if o.LineWidth <= 0 {
			o.LineWidth = 0.13
		}
	}
}

// Converter 持有字体管理器等可复用状态。
type Converter struct {
	Fonts    *font.Manager
	FontDirs []string
	mu       sync.Mutex
}

// NewConverter 创建转换器。
func NewConverter(fontDirs []string) *Converter {
	return &Converter{Fonts: font.NewManager(font.Options{ExtraDirs: fontDirs}), FontDirs: append([]string(nil), fontDirs...)}
}

// AddFontDir 追加字体目录并重建字体管理器（界面"字体库"用）。
func (c *Converter) AddFontDir(dir string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, d := range c.FontDirs {
		if d == dir {
			return
		}
	}
	c.FontDirs = append(c.FontDirs, dir)
	c.Fonts = font.NewManager(font.Options{ExtraDirs: c.FontDirs})
}

// Convert 把单个 .dwg/.dxf 转成 PDF，返回输出路径。
func (c *Converter) Convert(src, outDir string, o Options) (string, error) {
	o.applyQuality()
	ext := strings.ToLower(filepath.Ext(src))
	var dxfPath string
	var cleanup func()

	if ext == ".dxf" {
		dxfPath = src
		cleanup = func() {}
	} else {
		dopt := dwg.DefaultOptions()
		dopt.Prefer = o.Engine
		p, cl, err := dwg.Convert(src, dopt)
		if err != nil {
			return "", err
		}
		dxfPath = p
		cleanup = cl
	}
	defer cleanup()

	buf, err := os.ReadFile(dxfPath)
	if err != nil {
		return "", err
	}
	d, err := dxf.Parse(buf)
	if err != nil {
		return "", err
	}
	if len(d.Entities) == 0 {
		return "", fmt.Errorf("解析结果为空：文件可能不是有效的 DXF，或不含任何可绘制图元")
	}

	opt := buildRenderOptions(o)

	pdfw := pdf.New()
	ad := NewFontAdapter(c.Fonts, pdfw, font.NewGlyphMapper())
	r := render.New(d, opt, ad).WithWriter(pdfw)
	w := r.Render()

	if outDir == "" {
		outDir = filepath.Dir(src)
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return "", err
	}
	base := strings.TrimSuffix(filepath.Base(src), filepath.Ext(src))
	dst := filepath.Join(outDir, base+".pdf")
	if o.SkipExisting {
		if _, err := os.Stat(dst); err == nil {
			return dst, nil
		}
	}
	return dst, w.Save(dst)
}

// ConvertBytes 把内存中的 DXF 字节转成 PDF 字节（Web UI 上传用）。
func (c *Converter) ConvertBytes(name string, data []byte, o Options) ([]byte, error) {
	o.applyQuality()
	d, err := dxf.Parse(data)
	if err != nil {
		return nil, err
	}
	if len(d.Entities) == 0 {
		return nil, fmt.Errorf("解析结果为空：文件可能不是有效的 DXF，或不含任何可绘制图元")
	}
	opt := buildRenderOptions(o)

	pdfw := pdf.New()
	ad := NewFontAdapter(c.Fonts, pdfw, font.NewGlyphMapper())
	r := render.New(d, opt, ad).WithWriter(pdfw)
	w := r.Render()

	tmp, err := os.CreateTemp("", "dwg2pdf-web-*.pdf")
	if err != nil {
		return nil, err
	}
	tmpName := tmp.Name()
	tmp.Close()
	defer os.Remove(tmpName)
	if err := w.Save(tmpName); err != nil {
		return nil, err
	}
	return os.ReadFile(tmpName)
}

// FontAdapter 把字体管理器适配为渲染器的字体解析接口。
type FontAdapter struct {
	m     *font.Manager
	w     *pdf.Writer
	gm    *font.GlyphMapper
	byIdx map[int]*font.Font
	mu    sync.Mutex
}

// NewFontAdapter 创建适配器。
func NewFontAdapter(m *font.Manager, w *pdf.Writer, gm *font.GlyphMapper) *FontAdapter {
	return &FontAdapter{m: m, w: w, gm: gm, byIdx: make(map[int]*font.Font)}
}

// FontIndex 返回字体在 PDF 中的索引。
func (a *FontAdapter) FontIndex(name string) int {
	f, ok := a.m.Resolve(name)
	if !ok || f == nil {
		return 0
	}
	a.gm.Prepare(f.Name, f.Data)
	idx := a.w.AddFont(f.Name, f.Data)
	a.mu.Lock()
	a.byIdx[idx] = f
	a.mu.Unlock()
	return idx
}

// Glyph 返回字形查找回调。
func (a *FontAdapter) Glyph(fontIdx int) func(r rune) uint16 {
	a.mu.Lock()
	f, ok := a.byIdx[fontIdx]
	a.mu.Unlock()
	if !ok || f == nil {
		return nil
	}
	gm := a.gm
	name := f.Name
	return func(r rune) uint16 { return gm.GID(name, r) }
}

// buildRenderOptions 把管线参数映射为渲染参数（含裁剪区域）。
func buildRenderOptions(o Options) render.Options {
	opt := render.DefaultOptions()
	opt.Monochrome = o.Monochrome
	opt.LineWidth = o.LineWidth
	opt.Margin = o.Margin
	opt.PaperW, opt.PaperH = o.PaperW, o.PaperH
	opt.Scale = o.Scale
	opt.Workers = runtime.NumCPU()
	opt.HasClip = o.HasClip
	opt.ClipMinX, opt.ClipMinY = o.ClipMinX, o.ClipMinY
	opt.ClipMaxX, opt.ClipMaxY = o.ClipMaxX, o.ClipMaxY
	return opt
}
