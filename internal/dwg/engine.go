package dwg

import (
	"archive/zip"
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

// EngineKind 标识一种 DWG→DXF 引擎。
type EngineKind string

const (
	EngineLibreDWG EngineKind = "libredwg"
	EngineODA      EngineKind = "oda"
	EngineAuto     EngineKind = "auto"
)

// LibreDWG 便携版下载地址（官方 GitHub Release）。
const libreDWGWinURL = "https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14-win64.zip"

// ODA 官方下载页（需要人工安装/同意许可）。
const odaDownloadPage = "https://www.opendesign.com/guestfiles/oda_file_converter"

// EngineInfo 描述一个引擎的可用性与位置。
type EngineInfo struct {
	Kind      EngineKind `json:"kind"`
	Name      string     `json:"name"`
	Path      string     `json:"path,omitempty"`
	Available bool       `json:"available"`
	Source    string     `json:"source,omitempty"` // bundled / installed / PATH / env
	Note      string     `json:"note,omitempty"`
}

// Engines 返回全部引擎的当前状态（不论是否可用），供界面展示与选择。
func Engines() []EngineInfo {
	out := []EngineInfo{}
	var libre, oda *EngineInfo
	for _, c := range Discover() {
		info := EngineInfo{Kind: EngineKind(engineOf(c.Kind)), Available: true, Path: c.Path, Source: c.Source}
		if c.Kind == KindLibreDWG {
			info.Name = "GNU LibreDWG (dwg2dxf)"
			libre = &info
		} else {
			info.Name = "ODA File Converter"
			oda = &info
		}
		out = append(out, info)
	}
	if libre == nil {
		out = append(out, EngineInfo{
			Kind: EngineLibreDWG, Name: "GNU LibreDWG (dwg2dxf)", Available: false,
			Note: downloadHintLibre(),
		})
	}
	if oda == nil {
		out = append(out, EngineInfo{
			Kind: EngineODA, Name: "ODA File Converter", Available: false,
			Note: "免费但需手动安装；对 AutoCAD/天正等真实 DWG 兼容性最好。",
		})
	}
	return out
}

func downloadHintLibre() string {
	switch runtime.GOOS {
	case "windows":
		return "可一键下载便携版（免安装，约 33 MB）到程序目录。"
	case "linux":
		return "发行版包：sudo apt install libredwg-tools；或把 dwg2dxf 放到程序目录。"
	default:
		return "macOS 无官方预编译包：brew install libredwg（或源码编译），把 dwg2dxf 放到程序目录。"
	}
}

// DownloadLibreDWG 下载并解压 LibreDWG 便携版到 targetDir/libredwg。
// progress 回调接收已下载字节数（可为 nil）。返回最终目录。
func DownloadLibreDWG(targetDir string, progress func(done, total int64)) (string, error) {
	if runtime.GOOS != "windows" {
		return "", fmt.Errorf("一键下载仅支持 Windows；其它平台请按提示手动安装")
	}
	dest := filepath.Join(targetDir, "libredwg")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Minute)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, libreDWGWinURL, nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("下载失败（网络原因可稍后重试，或手动下载后解压到 %s）：%w", dest, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("下载失败：HTTP %d", resp.StatusCode)
	}
	var buf bytes.Buffer
	total := resp.ContentLength
	if _, err := io.Copy(&countingWriter{w: &buf, fn: progress, t: total}, resp.Body); err != nil {
		return "", err
	}
	// 解压最小依赖集
	zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		return "", fmt.Errorf("压缩包损坏：%w", err)
	}
	want := map[string]bool{"dwg2dxf.exe": true, "libredwg-0.dll": true, "libiconv-2.dll": true}
	got := 0
	for _, f := range zr.File {
		base := filepath.Base(f.Name)
		if !want[base] || f.FileInfo().IsDir() {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			continue
		}
		out, err := os.Create(filepath.Join(dest, base))
		if err != nil {
			rc.Close()
			return "", err
		}
		if _, err := io.Copy(out, rc); err != nil {
			out.Close()
			rc.Close()
			return "", err
		}
		out.Close()
		rc.Close()
		got++
	}
	if got != len(want) {
		return "", fmt.Errorf("解压不完整：%d/%d 个文件", got, len(want))
	}
	return dest, nil
}

type countingWriter struct {
	w  io.Writer
	fn func(done, total int64)
	t  int64
	n  int64
}

func (c *countingWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += int64(n)
	if c.fn != nil {
		c.fn(c.n, c.t)
	}
	return n, err
}

// ODADownloadURL 返回 ODA 官方下载页。
func ODADownloadURL() string { return odaDownloadPage }
