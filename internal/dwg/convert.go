package dwg

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"
)

// Options 转换参数。
type Options struct {
	// Timeout 单个文件转换超时（0 表示默认 600 秒）
	Timeout time.Duration
	// OutVersion 目标 DXF 版本（仅 ODA 使用）
	OutVersion string
}

// DefaultOptions 默认参数。
func DefaultOptions() Options {
	return Options{Timeout: 600 * time.Second, OutVersion: "ACAD2018"}
}

// Convert 把 DWG 转成 DXF，返回临时 DXF 路径与清理函数。
//
// 它会依次尝试已发现的转换器：LibreDWG 优先（免安装、随包分发），
// 失败后自动回退到 ODA File Converter（对国产 CAD 图纸兼容性最好）。
func Convert(src string, opt Options) (string, func(), error) {
	if opt.Timeout <= 0 {
		opt.Timeout = 600 * time.Second
	}
	if opt.OutVersion == "" {
		opt.OutVersion = "ACAD2018"
	}

	list := Discover()
	if len(list) == 0 {
		return "", nil, fmt.Errorf(
			"未找到 DWG 转换器\n" +
				"  方案一（推荐）：安装 ODA File Converter（免费）\n" +
				"    https://www.opendesign.com/guestfiles/oda_file_converter\n" +
				"  方案二：把 GNU LibreDWG 的 dwg2dxf 放到本程序同目录，或设置 DWG2PDF_CONVERTER")
	}

	tmpDir, err := os.MkdirTemp("", "dwg2pdf-*")
	if err != nil {
		return "", nil, err
	}
	cleanup := func() { os.RemoveAll(tmpDir) }

	var errs []string
	for _, c := range list {
		outDXF := filepath.Join(tmpDir, fmt.Sprintf("out-%d.dxf", c.Kind))
		var cerr error
		switch c.Kind {
		case KindLibreDWG:
			cerr = runLibreDWG(c, src, outDXF, opt)
		case KindODA:
			cerr = runODA(c, src, tmpDir, outDXF, opt)
		}
		if cerr == nil && fileExists(outDXF) {
			st, _ := osStat(outDXF)
			if st.size > 0 {
				return outDXF, cleanup, nil
			}
			cerr = fmt.Errorf("转换器未产出有效内容")
		}
		if cerr == nil {
			cerr = fmt.Errorf("未产出 DXF")
		}
		errs = append(errs, fmt.Sprintf("%s(%s): %v", c.Kind, c.Path, cerr))
	}

	cleanup()
	return "", nil, fmt.Errorf("所有转换器均失败：\n  %s", joinLines(errs))
}

// runLibreDWG 调用 dwg2dxf。
func runLibreDWG(c Converter, src, outDXF string, opt Options) error {
	cmd := exec.Command(c.Path, "-y", "-o", outDXF, src)
	return runWithTimeout(cmd, opt.Timeout)
}

// runODA 调用 ODA File Converter：
//
//	ODAFileConverter <inDir> <outDir> <version> <format> <recurse> <audit> <filter>
func runODA(c Converter, src, tmpDir, outDXF string, opt Options) error {
	inDir := filepath.Join(tmpDir, "in")
	outDir := filepath.Join(tmpDir, "out")
	if err := os.MkdirAll(inDir, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	base := "input"
	inFile := filepath.Join(inDir, base+".dwg")
	if err := os.WriteFile(inFile, data, 0o644); err != nil {
		return err
	}
	cmd := exec.Command(c.Path, inDir, outDir, opt.OutVersion, "DXF", "0", "1", "*.DWG")
	if err := runWithTimeout(cmd, opt.Timeout); err != nil {
		return err
	}
	produced := filepath.Join(outDir, base+".dxf")
	if !fileExists(produced) {
		return fmt.Errorf("ODA 未产出 %s", produced)
	}
	// 移到最终路径，供统一校验
	return os.Rename(produced, outDXF)
}

// runWithTimeout 执行命令并施加超时。
func runWithTimeout(cmd *exec.Cmd, timeout time.Duration) error {
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Start(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()

	select {
	case err := <-done:
		return err
	case <-time.After(timeout):
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
		<-done
		return fmt.Errorf("转换超时（%s）", timeout)
	}
}

func joinLines(s []string) string {
	out := ""
	for i, v := range s {
		if i > 0 {
			out += "\n  "
		}
		out += v
	}
	return out
}

var _ = runtime.GOOS
