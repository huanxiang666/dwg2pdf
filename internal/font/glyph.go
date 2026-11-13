// Package font 提供 SHX/TTF 字形映射，把 Unicode 转成 GlyphID，
// 供 PDF 的 CIDToGIDMap 使用。
package font

import (
	"sync"

	"golang.org/x/image/font"
	"golang.org/x/image/font/sfnt"
	"golang.org/x/image/math/fixed"
)

// GlyphMapper 把字符映射为字形 ID。
type GlyphMapper struct {
	mu    sync.Mutex
	fonts map[string]*mappedFont
}

type mappedFont struct {
	sf     *sfnt.Font
	buf    []byte
	hasKey bool
}

// NewGlyphMapper 创建映射器。
func NewGlyphMapper() *GlyphMapper {
	return &GlyphMapper{fonts: make(map[string]*mappedFont)}
}

// Prepare 解析字体数据，建立 cmap 索引。
func (m *GlyphMapper) Prepare(name string, data []byte) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.fonts[name]; ok {
		return nil
	}
	sf, err := sfnt.Parse(data)
	if err != nil {
		return err
	}
	m.fonts[name] = &mappedFont{sf: sf, buf: data, hasKey: true}
	return nil
}

// GID 返回字符的字形 ID（找不到返回 0 即 .notdef）。
func (m *GlyphMapper) GID(fontName string, r rune) uint16 {
	m.mu.Lock()
	f, ok := m.fonts[fontName]
	m.mu.Unlock()
	if !ok || f == nil || !f.hasKey {
		return 0
	}
	idx, err := f.sf.GlyphIndex(nil, r)
	if err != nil || idx == 0 {
		return 0
	}
	if idx > 0xFFFF {
		return 0
	}
	return uint16(idx)
}

// HasGlyph 判断字体是否包含该字符。
func (m *GlyphMapper) HasGlyph(fontName string, r rune) bool {
	return m.GID(fontName, r) != 0
}

// Advance 返回字符前进宽度（以 1000 为单位）。
func (m *GlyphMapper) Advance(fontName string, r rune) float64 {
	m.mu.Lock()
	f, ok := m.fonts[fontName]
	m.mu.Unlock()
	if !ok || f == nil {
		return 1000
	}
	idx, err := f.sf.GlyphIndex(nil, r)
	if err != nil {
		return 1000
	}
	adv, err := f.sf.GlyphAdvance(nil, idx, fixed.I(1000), font.HintingNone)
	if err != nil {
		return 1000
	}
	return float64(adv) / 64 * 1000 / 1000
}

// UnitsPerEm 返回字体的单位数。
func (m *GlyphMapper) UnitsPerEm(fontName string) float64 {
	m.mu.Lock()
	f, ok := m.fonts[fontName]
	m.mu.Unlock()
	if !ok || f == nil {
		return 1000
	}
	return float64(f.sf.UnitsPerEm())
}
