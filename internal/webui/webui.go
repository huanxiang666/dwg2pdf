// Package webui 提供 dwg2pdf 的本地图形界面（内嵌 Web UI）。
//
// 对标 Python 版 Tk 界面的功能面：文件列表、SVG 预览（缩放/平移/框选导出）、
// 引擎选择与一键下载、环境自检、质量档/纸张/黑白/并行/输出目录/字体库、
// 日志与状态栏。所有处理都在本机完成。
package webui

import (
	"archive/zip"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/huanxiang666/dwg2pdf/internal/dwg"
	"github.com/huanxiang666/dwg2pdf/internal/dxf"
	"github.com/huanxiang666/dwg2pdf/internal/pipeline"
	"github.com/huanxiang666/dwg2pdf/internal/preview"
)

// Handler 返回 GUI 的 HTTP handler。
func Handler(conv *pipeline.Converter, version string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", handleIndex)
	mux.HandleFunc("/api/check", func(w http.ResponseWriter, r *http.Request) { handleCheck(w, conv, version) })
	mux.HandleFunc("/api/engines", func(w http.ResponseWriter, r *http.Request) { handleEngines(w) })
	mux.HandleFunc("/api/engine/download", func(w http.ResponseWriter, r *http.Request) { handleEngineDownload(w, r) })
	mux.HandleFunc("/api/convert", func(w http.ResponseWriter, r *http.Request) { handleConvert(w, r, conv) })
	mux.HandleFunc("/api/preview", func(w http.ResponseWriter, r *http.Request) { handlePreview(w, r) })
	mux.HandleFunc("/api/export-region", func(w http.ResponseWriter, r *http.Request) { handleExportRegion(w, r, conv) })
	mux.HandleFunc("/api/fonts", func(w http.ResponseWriter, r *http.Request) { handleFonts(w, conv) })
	mux.HandleFunc("/api/fonts/add", func(w http.ResponseWriter, r *http.Request) { handleFontsAdd(w, r, conv) })
	mux.HandleFunc("/api/open-dir", func(w http.ResponseWriter, r *http.Request) { handleOpenDir(w, r) })
	mux.HandleFunc("/api/version-check", func(w http.ResponseWriter, r *http.Request) { handleVersionCheck(w, version) })
	mux.HandleFunc("/dl", handleDownload)
	return mux
}

func handleIndex(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(indexHTML)
}

func handleCheck(w http.ResponseWriter, conv *pipeline.Converter, version string) {
	writeJSON(w, map[string]any{
		"ok":       true,
		"version":  version,
		"platform": runtime.GOOS + "/" + runtime.GOARCH,
		"engines":  dwg.Engines(),
		"fonts":    conv.Fonts.List(),
	})
}

func handleEngines(w http.ResponseWriter) {
	writeJSON(w, map[string]any{"ok": true, "engines": dwg.Engines(), "odaPage": dwg.ODADownloadURL()})
}

// handleEngineDownload 一键下载引擎：LibreDWG 便携版直接落到程序目录；
// ODA 需要人工安装，返回官方页面由前端打开。
func handleEngineDownload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "仅支持 POST")
		return
	}
	var req struct {
		Kind string `json:"kind"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		writeErr(w, http.StatusBadRequest, "请求格式错误")
		return
	}
	switch req.Kind {
	case "libredwg":
		exe, err := os.Executable()
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		dir, err := dwg.DownloadLibreDWG(filepath.Dir(exe), nil)
		if err != nil {
			writeErr(w, http.StatusBadGateway, err.Error())
			return
		}
		writeJSON(w, map[string]any{"ok": true, "dir": dir, "message": "LibreDWG 已就绪，无需重启即可使用"})
	case "oda":
		writeJSON(w, map[string]any{"ok": true, "url": dwg.ODADownloadURL(), "message": "ODA 需手动安装：下载后按向导安装，装完回到本界面点「环境检查」"})
	default:
		writeErr(w, http.StatusBadRequest, "未知引擎: "+req.Kind)
	}
}

// handlePreview 上传图纸返回 SVG 预览与图纸边界。
func handlePreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "仅支持 POST")
		return
	}
	if err := r.ParseMultipartForm(256 << 20); err != nil {
		writeErr(w, http.StatusBadRequest, "上传失败："+err.Error())
		return
	}
	files := r.MultipartForm.File["files"]
	if len(files) != 1 {
		writeErr(w, http.StatusBadRequest, "预览一次只支持一个文件")
		return
	}
	fh := files[0]
	ext := strings.ToLower(filepath.Ext(fh.Filename))
	src, err := fh.Open()
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	data, err := io.ReadAll(io.LimitReader(src, 256<<20))
	src.Close()
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	mono := r.FormValue("monochrome") == "true"

	var dxfBytes []byte
	if ext == ".dxf" {
		dxfBytes = data
	} else {
		tmp, err := os.CreateTemp("", "dwg2pdf-prev-*.dwg")
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		tmp.Write(data)
		tmp.Close()
		defer os.Remove(tmp.Name())
		p, cleanup, cerr := dwg.Convert(tmp.Name(), dwg.Options{Prefer: r.FormValue("engine")})
		if cerr != nil {
			writeErr(w, http.StatusUnprocessableEntity, "DWG 转换失败："+cerr.Error())
			return
		}
		dxfBytes, err = os.ReadFile(p)
		cleanup()
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
	}
	d, err := dxf.Parse(dxfBytes)
	if err != nil {
		writeErr(w, http.StatusUnprocessableEntity, "DXF 解析失败："+err.Error())
		return
	}
	if len(d.Entities) == 0 {
		writeErr(w, http.StatusUnprocessableEntity, "图纸不含任何可绘制图元")
		return
	}
	res := preview.Build(d.Flatten(), mono)
	// SVG 较大：支持 gzip 时压缩传输
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Content-Encoding", "gzip")
		gz := gzip.NewWriter(w)
		json.NewEncoder(gz).Encode(map[string]any{"ok": true, "preview": res})
		gz.Close()
		return
	}
	writeJSON(w, map[string]any{"ok": true, "preview": res})
}

// handleExportRegion 按图纸坐标矩形导出局部矢量 PDF（框选导出）。
func handleExportRegion(w http.ResponseWriter, r *http.Request, conv *pipeline.Converter) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "仅支持 POST")
		return
	}
	if err := r.ParseMultipartForm(256 << 20); err != nil {
		writeErr(w, http.StatusBadRequest, "上传失败："+err.Error())
		return
	}
	files := r.MultipartForm.File["files"]
	if len(files) != 1 {
		writeErr(w, http.StatusBadRequest, "一次只支持一个文件")
		return
	}
	// 区域用四个数值字段传递（避免 multipart 里 JSON 引号被各客户端转义差异影响）
	var rect struct {
		MinX float64
		MinY float64
		MaxX float64
		MaxY float64
	}
	parseErr := false
	for _, kv := range []struct {
		name string
		dst  *float64
	}{{"minX", &rect.MinX}, {"minY", &rect.MinY}, {"maxX", &rect.MaxX}, {"maxY", &rect.MaxY}} {
		v, err := strconv.ParseFloat(r.FormValue(kv.name), 64)
		if err != nil {
			parseErr = true
			break
		}
		*kv.dst = v
	}
	if parseErr {
		writeErr(w, http.StatusBadRequest, "rect 参数错误：需要 minX/minY/maxX/maxY 四个数值字段")
		return
	}
	if !(rect.MaxX > rect.MinX) || !(rect.MaxY > rect.MinY) {
		writeErr(w, http.StatusBadRequest, "框选区域无效")
		return
	}
	fh := files[0]
	src, err := fh.Open()
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	data, err := io.ReadAll(io.LimitReader(src, 256<<20))
	src.Close()
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	ext := strings.ToLower(filepath.Ext(fh.Filename))
	opt := pipeline.DefaultOptions()
	opt.Engine = r.FormValue("engine")
	opt.Monochrome = r.FormValue("monochrome") == "true"
	opt.HasClip = true
	opt.ClipMinX, opt.ClipMinY = rect.MinX, rect.MinY
	opt.ClipMaxX, opt.ClipMaxY = rect.MaxX, rect.MaxY
	opt.PaperW, opt.PaperH = 0, 0 // 区域自适应纸张

	var pdfBytes []byte
	if ext == ".dxf" {
		pdfBytes, err = conv.ConvertBytes(fh.Filename, data, opt)
	} else {
		tmp, cerr := os.CreateTemp("", "dwg2pdf-reg-*.dwg")
		if cerr != nil {
			writeErr(w, http.StatusInternalServerError, cerr.Error())
			return
		}
		tmp.Write(data)
		tmp.Close()
		defer os.Remove(tmp.Name())
		p, cleanup, cerr := dwg.Convert(tmp.Name(), dwg.Options{Prefer: opt.Engine})
		if cerr != nil {
			writeErr(w, http.StatusUnprocessableEntity, cerr.Error())
			return
		}
		dxfBytes, rerr := os.ReadFile(p)
		cleanup()
		if rerr != nil {
			writeErr(w, http.StatusInternalServerError, rerr.Error())
			return
		}
		pdfBytes, err = conv.ConvertBytes(fh.Filename, dxfBytes, opt)
	}
	if err != nil {
		writeErr(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	id := storeResult(strings.TrimSuffix(filepath.Base(fh.Filename), filepath.Ext(fh.Filename))+"-区域.pdf", pdfBytes)
	writeJSON(w, map[string]any{"ok": true, "download": "/dl?id=" + id})
}

func handleFonts(w http.ResponseWriter, conv *pipeline.Converter) {
	writeJSON(w, map[string]any{"ok": true, "fonts": conv.Fonts.List(), "dirs": conv.FontDirs})
}

func handleFontsAdd(w http.ResponseWriter, r *http.Request, conv *pipeline.Converter) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "仅支持 POST")
		return
	}
	var req struct {
		Dir string `json:"dir"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil || req.Dir == "" {
		writeErr(w, http.StatusBadRequest, "需要提供 dir")
		return
	}
	st, err := os.Stat(req.Dir)
	if err != nil || !st.IsDir() {
		writeErr(w, http.StatusBadRequest, "目录不存在: "+req.Dir)
		return
	}
	conv.AddFontDir(req.Dir)
	writeJSON(w, map[string]any{"ok": true, "fonts": conv.Fonts.List(), "dirs": conv.FontDirs})
}

func handleOpenDir(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "仅支持 POST")
		return
	}
	var req struct {
		Path string `json:"path"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil || req.Path == "" {
		writeErr(w, http.StatusBadRequest, "需要提供 path")
		return
	}
	if st, err := os.Stat(req.Path); err != nil || !st.IsDir() {
		writeErr(w, http.StatusBadRequest, "目录不存在")
		return
	}
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("explorer.exe", req.Path)
	case "darwin":
		cmd = exec.Command("open", req.Path)
	default:
		cmd = exec.Command("xdg-open", req.Path)
	}
	if err := cmd.Start(); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	go func() { _ = cmd.Wait() }()
	writeJSON(w, map[string]any{"ok": true})
}

func handleVersionCheck(w http.ResponseWriter, version string) {
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Get("https://api.github.com/repos/huanxiang666/dwg2pdf/releases/latest")
	if err != nil {
		writeJSON(w, map[string]any{"ok": false, "error": "网络不可用：" + err.Error(), "current": version})
		return
	}
	defer resp.Body.Close()
	var info struct {
		TagName string `json:"tag_name"`
		HTMLURL string `json:"html_url"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&info); err != nil {
		writeJSON(w, map[string]any{"ok": false, "error": "解析失败", "current": version})
		return
	}
	latest := strings.TrimPrefix(info.TagName, "v")
	writeJSON(w, map[string]any{
		"ok": true, "current": version, "latest": latest, "url": info.HTMLURL,
		"updateAvailable": latest != version && latest != "",
	})
}

// ---------------------------------------------------------------- convert

type convResultItem struct {
	Name string  `json:"name"`
	Err  string  `json:"err,omitempty"`
	Sec  float64 `json:"sec,omitempty"`
}

func handleConvert(w http.ResponseWriter, r *http.Request, conv *pipeline.Converter) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "仅支持 POST")
		return
	}
	if err := r.ParseMultipartForm(512 << 20); err != nil {
		writeErr(w, http.StatusRequestEntityTooLarge, "上传失败或文件过大："+err.Error())
		return
	}
	opt := pipeline.DefaultOptions()
	opt.Monochrome = r.FormValue("monochrome") == "true" || r.FormValue("monochrome") == "1"
	opt.Engine = orDefault(r.FormValue("engine"), "auto")
	opt.Quality = orDefault(r.FormValue("quality"), "balanced")
	opt.SkipExisting = r.FormValue("skipExisting") == "true"
	switch strings.ToLower(r.FormValue("paper")) {
	case "a0":
		opt.PaperW, opt.PaperH = 841, 1189
	case "a1":
		opt.PaperW, opt.PaperH = 594, 841
	case "a2":
		opt.PaperW, opt.PaperH = 420, 594
	case "a3":
		opt.PaperW, opt.PaperH = 297, 420
	case "a4":
		opt.PaperW, opt.PaperH = 210, 297
	}
	outDir := r.FormValue("outdir")
	if outDir != "" {
		if st, err := os.Stat(outDir); err != nil || !st.IsDir() {
			writeErr(w, http.StatusBadRequest, "输出目录不存在: "+outDir)
			return
		}
	}

	tmpDir, err := os.MkdirTemp("", "dwg2pdf-web-")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	defer os.RemoveAll(tmpDir)

	files := r.MultipartForm.File["files"]
	if len(files) == 0 {
		writeErr(w, http.StatusBadRequest, "请先添加 .dwg 或 .dxf 文件")
		return
	}

	results := make([]convResultItem, 0, len(files))
	var pdfs []namedBytes

	for _, fh := range files {
		base := filepath.Base(fh.Filename)
		ext := strings.ToLower(filepath.Ext(base))
		if ext != ".dwg" && ext != ".dxf" {
			results = append(results, convResultItem{Name: base, Err: "不支持的类型（仅 .dwg / .dxf）"})
			continue
		}
		src, err := fh.Open()
		if err != nil {
			results = append(results, convResultItem{Name: base, Err: err.Error()})
			continue
		}
		data, err := io.ReadAll(io.LimitReader(src, 512<<20))
		src.Close()
		if err != nil {
			results = append(results, convResultItem{Name: base, Err: err.Error()})
			continue
		}
		t0 := time.Now()
		var pdfBytes []byte
		var cerr error
		if ext == ".dxf" {
			pdfBytes, cerr = conv.ConvertBytes(base, data, opt)
		} else {
			tmp := filepath.Join(tmpDir, sanitizeHeader(base))
			if werr := os.WriteFile(tmp, data, 0o600); werr != nil {
				cerr = werr
			} else {
				var out string
				out, cerr = conv.Convert(tmp, orDefault(outDir, tmpDir), opt)
				if cerr == nil && outDir == "" {
					pdfBytes, cerr = os.ReadFile(out)
				} else if cerr == nil {
					pdfBytes, cerr = os.ReadFile(out)
				}
			}
		}
		sec := time.Since(t0).Seconds()
		if cerr != nil {
			results = append(results, convResultItem{Name: base, Err: cerr.Error(), Sec: sec})
			continue
		}
		outName := strings.TrimSuffix(base, filepath.Ext(base)) + ".pdf"
		pdfs = append(pdfs, namedBytes{outName, pdfBytes})
		results = append(results, convResultItem{Name: base, Sec: sec})
	}

	resp := map[string]any{"ok": true, "results": results}
	if len(pdfs) == 1 {
		id := storeResult(pdfs[0].name, pdfs[0].data)
		resp["download"] = "/dl?id=" + id
		resp["filename"] = pdfs[0].name
	} else if len(pdfs) > 1 {
		var buf bytes.Buffer
		zw := zip.NewWriter(&buf)
		for _, p := range pdfs {
			fw, err := zw.Create(p.name)
			if err != nil {
				continue
			}
			fw.Write(p.data)
		}
		zw.Close()
		id := storeResult("dwg2pdf-results.zip", buf.Bytes())
		resp["download"] = "/dl?id=" + id
		resp["filename"] = "dwg2pdf-results.zip"
	}
	writeJSON(w, resp)
}

type namedBytes struct {
	name string
	data []byte
}

func orDefault(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}

// ---------------------------------------------------------------- results

var resultStore = struct {
	sync.Mutex
	items map[string]storedFile
	seq   int
}{items: map[string]storedFile{}}

type storedFile struct {
	name string
	data []byte
	at   time.Time
}

func storeResult(name string, data []byte) string {
	resultStore.Lock()
	defer resultStore.Unlock()
	resultStore.seq++
	id := fmt.Sprintf("r%d-%d", time.Now().Unix(), resultStore.seq)
	resultStore.items[id] = storedFile{name: name, data: data, at: time.Now()}
	for k, v := range resultStore.items {
		if time.Since(v.at) > time.Hour {
			delete(resultStore.items, k)
		}
	}
	return id
}

func handleDownload(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	resultStore.Lock()
	f, ok := resultStore.items[id]
	resultStore.Unlock()
	if !ok {
		writeErr(w, http.StatusNotFound, "结果不存在或已过期")
		return
	}
	ct := "application/pdf"
	if strings.HasSuffix(f.name, ".zip") {
		ct = "application/zip"
	}
	w.Header().Set("Content-Type", ct)
	w.Header().Set("Content-Length", fmt.Sprintf("%d", len(f.data)))
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, sanitizeHeader(f.name)))
	w.Write(f.data)
}

func sanitizeHeader(s string) string {
	return strings.Map(func(r rune) rune {
		if r == '"' || r == '\r' || r == '\n' {
			return '_'
		}
		return r
	}, s)
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": msg})
}
