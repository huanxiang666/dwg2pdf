// Package dxf 提供高性能 DXF 解析。
// 40MB 级图纸的扫描目标是百毫秒量级。
package dxf

import (
	"strconv"
)

// Scanner 以组码/值对形式流式扫描 DXF 字节流。
type Scanner struct {
	buf []byte
	pos int
}

// NewScanner 创建扫描器（自动跳过 UTF-8 BOM）。
func NewScanner(buf []byte) *Scanner {
	if len(buf) >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF {
		buf = buf[3:]
	}
	return &Scanner{buf: buf}
}

// nextLine 返回下一行内容（不含换行符）。
func (s *Scanner) nextLine() ([]byte, bool) {
	if s.pos >= len(s.buf) {
		return nil, false
	}
	start := s.pos
	for s.pos < len(s.buf) && s.buf[s.pos] != '\n' {
		s.pos++
	}
	end := s.pos
	if s.pos < len(s.buf) {
		s.pos++
	}
	if end > start && s.buf[end-1] == '\r' {
		end--
	}
	return s.buf[start:end], true
}

// atoi 容错解析：忽略前后空白。
func atoi(b []byte) (int, bool) {
	i := 0
	for i < len(b) && (b[i] == ' ' || b[i] == '\t') {
		i++
	}
	j := len(b)
	for j > i && (b[j-1] == ' ' || b[j-1] == '\t') {
		j--
	}
	if i >= j {
		return 0, false
	}
	v, err := strconv.Atoi(string(b[i:j]))
	if err != nil {
		return 0, false
	}
	return v, true
}

// Next 返回下一个组码与值。
func (s *Scanner) Next() (int, []byte, bool) {
	for {
		codeLine, ok := s.nextLine()
		if !ok {
			return 0, nil, false
		}
		code, ok := atoi(codeLine)
		if !ok {
			continue // 跳过无法识别的行，保持同步
		}
		valueLine, ok := s.nextLine()
		if !ok {
			return 0, nil, false
		}
		return code, valueLine, true
	}
}

// Float 解析浮点数（忽略前后空白）。
func Float(b []byte) float64 {
	i := 0
	for i < len(b) && (b[i] == ' ' || b[i] == '\t') {
		i++
	}
	j := len(b)
	for j > i && (b[j-1] == ' ' || b[j-1] == '\t' || b[j-1] == '\r' || b[j-1] == '\n') {
		j--
	}
	if i >= j {
		return 0
	}
	v, err := strconv.ParseFloat(string(b[i:j]), 64)
	if err != nil {
		return 0
	}
	return v
}

// Int 解析整数（容错）。
func Int(b []byte) int {
	v, ok := atoi(b)
	if !ok {
		return 0
	}
	return v
}
