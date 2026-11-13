package main

import (
	"fmt"
	"os"
	"time"

	"github.com/huanxiang666/dwg2pdf/internal/dwg"
)

// convResult 单个文件转换结果。
type convResult struct {
	src, dst string
	err      error
	sec      float64
}

// dwgToDXF 把 DWG 转成临时 DXF，返回路径与清理函数。
func dwgToDXF(src string) (string, func(), error) {
	opt := dwg.DefaultOptions()
	if v := os.Getenv("DWG2PDF_TIMEOUT"); v != "" {
		var secs int
		if _, err := fmt.Sscanf(v, "%d", &secs); err == nil && secs > 0 {
			opt.Timeout = time.Duration(secs) * time.Second
		}
	}
	return dwg.Convert(src, opt)
}

// converterInfo 返回转换器描述，用于自检输出。
func converterInfo() string {
	list := dwg.Discover()
	if len(list) == 0 {
		return "未找到 —— DWG 输入需要 ODA File Converter 或内置 LibreDWG"
	}
	s := ""
	for i, c := range list {
		if i > 0 {
			s += "\n              "
		}
		s += fmt.Sprintf("%s (%s) %s", c.Kind, c.Source, c.Path)
	}
	return s
}
