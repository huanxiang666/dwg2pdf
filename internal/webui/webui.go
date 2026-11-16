// Package webui 提供 dwg2pdf 的本地图形界面（内嵌 Web UI）。
//
// 设计取向：双击 exe 后浏览器自动打开，拖入 .dwg/.dxf 即可转换并下载 PDF。
// 所有处理都在本机完成；DWG 依赖本机已安装的转换器（LibreDWG/ODA），
// 界面会明确显示当前转换器状态，避免"点了没反应"式的困惑。
package webui

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/huanxiang666/dwg2pdf/internal/dwg"
	"github.com/huanxiang666/dwg2pdf/internal/pipeline"
)

// Handler 返回 GUI 的 HTTP handler。conv 不能为 nil。
func Handler(conv *pipeline.Converter) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", handleIndex)
	mux.HandleFunc("/api/check", func(w http.ResponseWriter, r *http.Request) { handleCheck(w, conv) })
	mux.HandleFunc("/api/convert", func(w http.ResponseWriter, r *http.Request) { handleConvert(w, r, conv) })
	mux.HandleFunc("/dl", handleDownload)
	return mux
}

func handleIndex(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(indexHTML)
}

func handleCheck(w http.ResponseWriter, conv *pipeline.Converter) {
	converters := []string{}
	for _, c := range dwg.Discover() {
		converters = append(converters, fmt.Sprintf("%s (%s)", c.Kind, c.Source))
	}
	writeJSON(w, map[string]any{
		"ok":         true,
		"version":    "1.0.0",
		"converters": converters,
		"fonts":      conv.Fonts.List(),
	})
}

// resultStore 保存待下载的结果。
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
		http.Error(w, "result not found or expired", http.StatusNotFound)
		return
	}
	ct := "application/pdf"
	if strings.HasSuffix(f.name, ".zip") {
		ct = "application/zip"
	}
	w.Header().Set("Content-Type", ct)
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

// handleConvert 接收上传的 dwg/dxf，转换后返回下载链接。
func handleConvert(w http.ResponseWriter, r *http.Request, conv *pipeline.Converter) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	if err := r.ParseMultipartForm(512 << 20); err != nil {
		writeJSON(w, map[string]any{"ok": false, "error": "上传失败或文件过大：" + err.Error()})
		return
	}
	opt := pipeline.DefaultOptions()
	if r.FormValue("monochrome") == "true" || r.FormValue("monochrome") == "1" {
		opt.Monochrome = true
	}
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

	tmpDir, err := os.MkdirTemp("", "dwg2pdf-web-")
	if err != nil {
		writeJSON(w, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	defer os.RemoveAll(tmpDir)

	files := r.MultipartForm.File["files"]
	if len(files) == 0 {
		writeJSON(w, map[string]any{"ok": false, "error": "请先添加 .dwg 或 .dxf 文件"})
		return
	}

	type one struct {
		Name string  `json:"name"`
		Err  string  `json:"err,omitempty"`
		Sec  float64 `json:"sec,omitempty"`
	}
	results := make([]one, 0, len(files))
	var pdfs []struct {
		name string
		data []byte
	}

	for _, fh := range files {
		base := filepath.Base(fh.Filename)
		ext := strings.ToLower(filepath.Ext(base))
		if ext != ".dwg" && ext != ".dxf" {
			results = append(results, one{Name: base, Err: "不支持的类型（仅 .dwg / .dxf）"})
			continue
		}
		src, err := fh.Open()
		if err != nil {
			results = append(results, one{Name: base, Err: err.Error()})
			continue
		}
		data, err := io.ReadAll(io.LimitReader(src, 512<<20))
		src.Close()
		if err != nil {
			results = append(results, one{Name: base, Err: err.Error()})
			continue
		}
		t0 := time.Now()
		var pdfBytes []byte
		var cerr error
		if ext == ".dxf" {
			pdfBytes, cerr = conv.ConvertBytes(base, data, opt)
		} else {
			// DWG 需要先落盘交给外部转换器
			tmp := filepath.Join(tmpDir, sanitizeHeader(base))
			if werr := os.WriteFile(tmp, data, 0o600); werr != nil {
				cerr = werr
			} else {
				out, oerr := conv.Convert(tmp, tmpDir, opt)
				if oerr != nil {
					cerr = oerr
				} else {
					pdfBytes, cerr = os.ReadFile(out)
				}
			}
		}
		sec := time.Since(t0).Seconds()
		if cerr != nil {
			results = append(results, one{Name: base, Err: cerr.Error(), Sec: sec})
			continue
		}
		outName := strings.TrimSuffix(base, filepath.Ext(base)) + ".pdf"
		pdfs = append(pdfs, struct {
			name string
			data []byte
		}{outName, pdfBytes})
		results = append(results, one{Name: base, Sec: sec})
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

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	json.NewEncoder(w).Encode(v)
}

var _ = multipart.File(nil)
