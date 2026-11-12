// Command dwg2pdf 把 DWG/DXF 图纸批量转换为矢量 PDF。
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/huanxiang666/dwg2pdf/internal/dxf"
	"github.com/huanxiang666/dwg2pdf/internal/font"
	"github.com/huanxiang666/dwg2pdf/internal/pdf"
	"github.com/huanxiang666/dwg2pdf/internal/render"
)

const version = "1.0.0"

func main() {
	os.Exit(run())
}

type cliOpts struct {
	outDir    string
	recursive bool
	mono      bool
	paper     string
	scale     float64
	margin    float64
	jobs      int
	skipExist bool
	fontDirs  multiFlag
	report    string
	showVer   bool
	check     bool
	listFonts bool
	lineWidth float64
}

func run() int {
	var o cliOpts
	flag.StringVar(&o.outDir, "o", "", "输出目录（默认源文件旁）")
	flag.BoolVar(&o.recursive, "r", false, "递归扫描目录")
	flag.BoolVar(&o.mono, "monochrome", false, "黑白输出")
	flag.StringVar(&o.paper, "paper", "auto", "纸张: auto|a0|a1|a2|a3|a4|letter")
	flag.Float64Var(&o.scale, "scale", 1, "内容缩放系数")
	flag.Float64Var(&o.margin, "margin", -1, "页边距 mm（默认自动）")
	flag.IntVar(&o.jobs, "j", 0, "并行进程数（0=CPU 核数）")
	flag.BoolVar(&o.skipExist, "skip-existing", false, "跳过已存在的 PDF")
	flag.Var(&o.fontDirs, "font-dir", "追加字体目录（可重复）")
	flag.StringVar(&o.report, "report", "", "输出 JSON 报告路径")
	flag.BoolVar(&o.showVer, "version", false, "显示版本")
	flag.BoolVar(&o.check, "check", false, "环境自检")
	flag.BoolVar(&o.listFonts, "list-fonts", false, "列出可用字体")
	flag.Float64Var(&o.lineWidth, "line-width", 0.13, "基础线宽 mm")
	flag.Parse()

	if o.showVer {
		fmt.Printf("dwg2pdf %s (%s/%s, go %s)\n", version, runtime.GOOS, runtime.GOARCH, runtime.Version())
		return 0
	}

	fm := font.NewManager(font.Options{ExtraDirs: o.fontDirs})

	if o.listFonts {
		fmt.Print(fm.Info())
		return 0
	}

	if o.check {
		return doCheck(fm)
	}

	inputs := flag.Args()
	if len(inputs) == 0 {
		fmt.Fprintln(os.Stderr, "用法: dwg2pdf [选项] <文件或目录>...")
		flag.PrintDefaults()
		return 2
	}

	files, err := collectFiles(inputs, o.recursive)
	if err != nil {
		fmt.Fprintln(os.Stderr, "扫描输入失败:", err)
		return 2
	}
	if len(files) == 0 {
		fmt.Fprintln(os.Stderr, "没有找到 .dwg / .dxf 文件")
		return 2
	}

	paperW, paperH := parsePaper(o.paper)
	margin := o.margin
	if margin < 0 {
		if o.paper == "auto" {
			margin = 0
		} else {
			margin = 5
		}
	}

	start := time.Now()
	results := make([]convResult, len(files))

	workers := o.jobs
	if workers <= 0 {
		workers = runtime.NumCPU()
	}
	if workers > len(files) {
		workers = len(files)
	}

	sem := make(chan struct{}, workers)
	var wg sync.WaitGroup
	var mu sync.Mutex
	done := 0

	for i, f := range files {
		wg.Add(1)
		go func(idx int, src string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			t0 := time.Now()
			dst, err := convertOne(src, o, paperW, paperH, margin, fm)
			results[idx] = convResult{src: src, dst: dst, err: err, sec: time.Since(t0).Seconds()}

			mu.Lock()
			done++
			n := done
			mu.Unlock()
			if err != nil {
				fmt.Printf("[%d/%d] FAIL  %s: %v\n", n, len(files), filepath.Base(src), err)
			} else {
				fmt.Printf("[%d/%d] OK    %s (%.2fs)\n", n, len(files), filepath.Base(src), results[idx].sec)
			}
		}(i, f)
	}
	wg.Wait()

	okN, failN := 0, 0
	for _, r := range results {
		if r.err != nil {
			failN++
		} else {
			okN++
		}
	}
	el := time.Since(start).Seconds()
	fmt.Printf("\n完成: %d 成功, %d 失败, 用时 %.2fs\n", okN, failN, el)

	if o.report != "" {
		if err := writeReport(o.report, results, el, okN, failN); err != nil {
			fmt.Fprintln(os.Stderr, "写报告失败:", err)
		}
	}
	if failN > 0 {
		return 1
	}
	return 0
}

func convertOne(src string, o cliOpts, pw, ph, margin float64, fm *font.Manager) (string, error) {
	ext := strings.ToLower(filepath.Ext(src))
	var dxfPath string
	var cleanup func()

	if ext == ".dxf" {
		dxfPath = src
		cleanup = func() {}
	} else {
		p, c, err := dwgToDXF(src)
		if err != nil {
			return "", err
		}
		dxfPath = p
		cleanup = c
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

	opt := render.DefaultOptions()
	opt.Monochrome = o.mono
	opt.LineWidth = o.lineWidth
	opt.Margin = margin
	opt.PaperW, opt.PaperH = pw, ph
	opt.Scale = o.scale
	opt.Workers = runtime.NumCPU()

	pdfw := pdf.New()
	ad := newFontAdapter(fm, pdfw, font.NewGlyphMapper())
	r := render.New(d, opt, ad).WithWriter(pdfw)
	w := r.Render()

	outDir := o.outDir
	if outDir == "" {
		outDir = filepath.Dir(src)
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return "", err
	}
	base := strings.TrimSuffix(filepath.Base(src), filepath.Ext(src))
	dst := filepath.Join(outDir, base+".pdf")
	if o.skipExist {
		if _, err := os.Stat(dst); err == nil {
			return dst, nil
		}
	}
	return dst, w.Save(dst)
}

// fontAdapter 把字体管理器适配为渲染器的字体解析接口。
type fontAdapter struct {
	m     *font.Manager
	w     *pdf.Writer
	gm    *font.GlyphMapper
	byIdx map[int]*font.Font
	mu    sync.Mutex
}

func newFontAdapter(m *font.Manager, w *pdf.Writer, gm *font.GlyphMapper) *fontAdapter {
	return &fontAdapter{m: m, w: w, gm: gm, byIdx: make(map[int]*font.Font)}
}

func (a *fontAdapter) FontIndex(name string) int {
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

func (a *fontAdapter) Glyph(fontIdx int) func(r rune) uint16 {
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

func collectFiles(inputs []string, recursive bool) ([]string, error) {
	var out []string
	seen := map[string]bool{}
	add := func(p string) {
		ext := strings.ToLower(filepath.Ext(p))
		if ext != ".dwg" && ext != ".dxf" {
			return
		}
		abs, _ := filepath.Abs(p)
		if seen[abs] {
			return
		}
		seen[abs] = true
		out = append(out, p)
	}
	for _, in := range inputs {
		st, err := os.Stat(in)
		if err != nil {
			return nil, err
		}
		if !st.IsDir() {
			add(in)
			continue
		}
		if recursive {
			err = filepath.WalkDir(in, func(p string, d os.DirEntry, err error) error {
				if err != nil {
					return nil
				}
				if !d.IsDir() {
					add(p)
				}
				return nil
			})
			if err != nil {
				return nil, err
			}
		} else {
			ents, err := os.ReadDir(in)
			if err != nil {
				return nil, err
			}
			for _, e := range ents {
				if !e.IsDir() {
					add(filepath.Join(in, e.Name()))
				}
			}
		}
	}
	return out, nil
}

func parsePaper(name string) (w, h float64) {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "", "auto":
		return 0, 0
	case "a0":
		return 841, 1189
	case "a1":
		return 594, 841
	case "a2":
		return 420, 594
	case "a3":
		return 297, 420
	case "a4":
		return 210, 297
	case "letter":
		return 216, 279
	case "legal":
		return 216, 356
	}
	return 0, 0
}

type multiFlag []string

func (m *multiFlag) String() string { return strings.Join(*m, ",") }
func (m *multiFlag) Set(v string) error {
	*m = append(*m, v)
	return nil
}

func doCheck(fm *font.Manager) int {
	fmt.Printf("dwg2pdf %s | %s/%s | go %s | %d 核\n",
		version, runtime.GOOS, runtime.GOARCH, runtime.Version(), runtime.NumCPU())
	fmt.Println("DXF 解析: OK (内置)")
	fmt.Println("PDF 输出: OK (内置)")
	fmt.Print(fm.Info())
	fmt.Printf("DWG 转换器: %s\n", converterInfo())
	return 0
}
