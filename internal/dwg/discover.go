package dwg

import (
	"fmt"
	"os/exec"
	"path/filepath"
	"runtime"
)

// Converter 描述一个可用的 DWG→DXF 转换后端。
type Converter struct {
	Path   string
	Kind   Kind
	Source string // 发现来源，便于诊断
}

// Kind 转换器类型。
type Kind int

const (
	// KindLibreDWG 是 GNU LibreDWG 的 dwg2dxf。
	KindLibreDWG Kind = iota
	// KindODA 是 ODA File Converter。
	KindODA
)

func (k Kind) String() string {
	switch k {
	case KindLibreDWG:
		return "LibreDWG"
	case KindODA:
		return "ODA File Converter"
	}
	return "unknown"
}

// Discover 按优先级查找可用的转换器：
//  1. 环境变量 DWG2PDF_CONVERTER
//  2. 与可执行文件同目录的内置 LibreDWG（随安装包分发）
//  3. PATH 中的 dwg2dxf
//  4. 系统安装的 ODA File Converter
//  5. PATH 中的 ODAFileConverter
//
// 返回全部已发现的转换器，调用方按顺序尝试。
func Discover() []Converter {
	var out []Converter
	seen := map[string]bool{}
	add := func(p string, k Kind, src string) {
		abs, err := filepath.Abs(p)
		if err != nil {
			abs = p
		}
		if seen[abs] {
			return
		}
		if _, err := exec.LookPath(abs); err != nil {
			// LookPath 对绝对路径也可用；失败说明文件不存在
			if st, e := osStat(abs); e != nil || st.isDir {
				return
			}
		}
		seen[abs] = true
		out = append(out, Converter{Path: abs, Kind: k, Source: src})
	}

	// 1. 环境变量
	if p := envGet("DWG2PDF_CONVERTER"); p != "" {
		add(p, guessKind(p), "DWG2PDF_CONVERTER")
	}

	// 2. 随包内置的 LibreDWG
	if exe, err := executablePath(); err == nil {
		dir := filepath.Dir(exe)
		for _, name := range exeNames("dwg2dxf") {
			for _, sub := range []string{"", "libredwg", filepath.Join("bin")} {
				p := filepath.Join(dir, sub, name)
				if fileExists(p) {
					add(p, KindLibreDWG, "bundled")
				}
			}
		}
	}

	// 3. PATH 中的 dwg2dxf
	for _, name := range exeNames("dwg2dxf") {
		if p, err := exec.LookPath(name); err == nil {
			add(p, KindLibreDWG, "PATH")
		}
	}

	// 4. 系统 ODA
	for _, p := range odaCandidates() {
		if fileExists(p) {
			add(p, KindODA, "installed")
		}
	}

	// 5. PATH 中的 ODA
	for _, name := range exeNames("ODAFileConverter") {
		if p, err := exec.LookPath(name); err == nil {
			add(p, KindODA, "PATH")
		}
	}

	return out
}

// Pick 返回优先级最高的转换器。
func Pick() (Converter, error) {
	all := Discover()
	if len(all) == 0 {
		return Converter{}, fmt.Errorf(
			"未找到 DWG 转换器。请安装 ODA File Converter，或把 LibreDWG 的 dwg2dxf 放到程序目录")
	}
	return all[0], nil
}

func guessKind(p string) Kind {
	base := filepath.Base(p)
	if containsFold(base, "dwg2dxf") {
		return KindLibreDWG
	}
	return KindODA
}

// odaCandidates 返回各平台 ODA File Converter 的常见安装路径。
func odaCandidates() []string {
	switch runtime.GOOS {
	case "windows":
		var out []string
		for _, root := range []string{envGet("ProgramFiles"), envGet("ProgramFiles(x86)")} {
			if root == "" {
				continue
			}
			base := filepath.Join(root, "ODA")
			if ents, err := readDir(base); err == nil {
				for _, e := range ents {
					if e.IsDir() {
						out = append(out, filepath.Join(base, e.Name(), "ODAFileConverter.exe"))
					}
				}
			}
		}
		return out
	case "darwin":
		return []string{
			"/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
			"/Applications/ODAFileConverter 26.4.app/Contents/MacOS/ODAFileConverter",
		}
	default:
		return []string{
			"/usr/bin/ODAFileConverter",
			"/usr/local/bin/ODAFileConverter",
			"/opt/ODAFileConverter/ODAFileConverter",
		}
	}
}

// exeNames 返回带/不带 .exe 的文件名候选。
func exeNames(base string) []string {
	if runtime.GOOS == "windows" {
		return []string{base + ".exe", base}
	}
	return []string{base}
}
