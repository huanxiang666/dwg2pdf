// Package font 提供可替换的字体系统。
//
// 设计目标：
//  1. 内置一套默认字库（思源黑体子集），开箱即用，零外部依赖
//  2. 支持用户通过 --font-dir 或配置文件替换为自己的 TTF/OTF
//  3. 支持 SHX 字体（CAD 专用）的路径映射
//  4. 字体按需加载、缓存，不重复解析
package font

import (
	"embed"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

//go:embed assets/*.ttf
var embedded embed.FS

// Font 描述一个已加载的字体。
type Font struct {
	Name   string
	Path   string
	Data   []byte
	Family string
	Style  string
}

// Manager 管理字体集合与查找。
type Manager struct {
	mu sync.RWMutex
	// 候选目录（按优先级）
	dirs []string
	// 已加载字体（按族名+样式索引）
	loaded map[string]*Font
	// 别名映射：CAD 字体名 -> 字体族名
	alias map[string]string
	// SHX 兼容映射
	shxMap map[string]string
}

// Options 管理器配置。
type Options struct {
	// 额外字体目录（用户通过 --font-dir 指定）
	ExtraDirs []string
	// 是否禁用内置字体
	NoEmbedded bool
}

// NewManager 创建字体管理器。
func NewManager(opt Options) *Manager {
	m := &Manager{
		loaded: make(map[string]*Font, 64),
		alias:  defaultAliases(),
		shxMap: defaultSHXMap(),
	}

	if !opt.NoEmbedded {
		m.loadEmbedded()
	}

	// 候选目录：用户目录优先，再系统目录
	m.dirs = append(m.dirs, opt.ExtraDirs...)
	m.dirs = append(m.dirs, systemFontDirs()...)
	return m
}

// systemFontDirs 返回各平台系统字体目录。
func systemFontDirs() []string {
	var dirs []string
	if d := os.Getenv("DWG2PDF_FONT_DIR"); d != "" {
		dirs = append(dirs, d)
	}
	// 配置文件旁的字库目录（便携模式）
	if exe, err := os.Executable(); err == nil {
		dirs = append(dirs, filepath.Join(filepath.Dir(exe), "fonts"))
	}
	switch {
	case isWindows():
		windir := os.Getenv("WINDIR")
		if windir == "" {
			windir = `C:\Windows`
		}
		dirs = append(dirs, filepath.Join(windir, "Fonts"))
	case isDarwin():
		dirs = append(dirs,
			"/System/Library/Fonts",
			"/Library/Fonts",
			filepath.Join(os.Getenv("HOME"), "Library/Fonts"),
		)
	default:
		dirs = append(dirs,
			"/usr/share/fonts",
			"/usr/local/share/fonts",
			filepath.Join(os.Getenv("HOME"), ".fonts"),
			filepath.Join(os.Getenv("HOME"), ".local/share/fonts"),
		)
	}
	return dirs
}

func isWindows() bool { return os.PathSeparator == '\\' }
func isDarwin() bool {
	_, err := os.Stat("/System/Library/Fonts")
	return err == nil
}

// loadEmbedded 加载内置字体。
func (m *Manager) loadEmbedded() {
	entries, err := embedded.ReadDir("assets")
	if err != nil {
		return
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".ttf") {
			continue
		}
		data, err := embedded.ReadFile("assets/" + e.Name())
		if err != nil {
			continue
		}
		base := strings.TrimSuffix(e.Name(), filepath.Ext(e.Name()))
		f := &Font{
			Name:   base,
			Path:   "embedded:" + e.Name(),
			Data:   data,
			Family: base,
		}
		m.loaded[strings.ToLower(base)] = f
	}
}

// AddDir 追加字体目录。
func (m *Manager) AddDir(d string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.dirs = append([]string{d}, m.dirs...)
}

// SetAlias 设置字体别名。
func (m *Manager) SetAlias(from, to string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.alias[strings.ToLower(from)] = to
}

// Resolve 根据 CAD 字体名查找可用字体。
// name 可能是 "simhei"、"gbcbig.shx"、"Arial" 等。空名返回默认字体。
func (m *Manager) Resolve(name string) (*Font, bool) {
	key := strings.ToLower(strings.TrimSpace(name))
	if key == "" {
		return m.Default()
	}
	m.mu.RLock()
	if f, ok := m.loaded[key]; ok {
		m.mu.RUnlock()
		return f, true
	}
	// 别名
	if target, ok := m.alias[key]; ok {
		key = strings.ToLower(target)
		if f, ok := m.loaded[key]; ok {
			m.mu.RUnlock()
			return f, true
		}
	}
	// SHX 映射
	if target, ok := m.shxMap[key]; ok {
		key = strings.ToLower(target)
		if f, ok := m.loaded[key]; ok {
			m.mu.RUnlock()
			return f, true
		}
	}
	m.mu.RUnlock()

	// 尝试从目录中按文件名查找
	if f := m.searchDirs(key); f != nil {
		return f, true
	}
	// 回退到默认字体
	return m.Default()
}

// searchDirs 在字体目录里按文件名查找。
func (m *Manager) searchDirs(name string) *Font {
	stem := strings.TrimSuffix(name, filepath.Ext(name))
	candidates := []string{name + ".ttf", name + ".otf", stem + ".ttf", stem + ".otf", stem + ".ttc"}

	m.mu.RLock()
	dirs := append([]string(nil), m.dirs...)
	m.mu.RUnlock()

	for _, d := range dirs {
		for _, c := range candidates {
			p := filepath.Join(d, c)
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				data, err := os.ReadFile(p)
				if err != nil {
					continue
				}
				f := &Font{Name: stem, Path: p, Data: data, Family: stem}
				m.mu.Lock()
				m.loaded[strings.ToLower(stem)] = f
				m.mu.Unlock()
				return f
			}
		}
	}
	return nil
}

// Default 返回默认字体。
func (m *Manager) Default() (*Font, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	// 优先内置
	for _, name := range []string{"default", "sourcehan", "noto"} {
		if f, ok := m.loaded[name]; ok {
			return f, true
		}
	}
	// 任意一个已加载的
	keys := make([]string, 0, len(m.loaded))
	for k := range m.loaded {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		return m.loaded[k], true
	}
	return nil, false
}

// List 列出当前可用的内置字体。
func (m *Manager) List() []string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]string, 0, len(m.loaded))
	for k := range m.loaded {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Info 返回字体系统状态摘要。
func (m *Manager) Info() string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	var b strings.Builder
	fmt.Fprintf(&b, "字体管理器: %d 个已加载, %d 个搜索目录\n", len(m.loaded), len(m.dirs))
	fmt.Fprintf(&b, "内置字体: %v\n", m.List())
	for i, d := range m.dirs {
		if i >= 5 {
			fmt.Fprintf(&b, "  ... 另有 %d 个目录\n", len(m.dirs)-5)
			break
		}
		fmt.Fprintf(&b, "  搜索目录[%d]: %s\n", i, d)
	}
	return b.String()
}

// defaultAliases 常见 CAD 字体别名。
func defaultAliases() map[string]string {
	return map[string]string{
		"txt":            "default",
		"txt.shx":        "default",
		"simplex":        "default",
		"romans":         "default",
		"romand":         "default",
		"arial":          "default",
		"helvetica":      "default",
		"times":          "default",
		"宋体":             "default",
		"simsun":         "default",
		"新宋体":            "default",
		"黑体":             "default",
		"simhei":         "default",
		"微软雅黑":           "default",
		"microsoftyahei": "default",
		"仿宋":             "default",
		"fangsong":       "default",
		"楷体":             "default",
		"kaiti":          "default",
	}
}

// defaultSHXMap 把 SHX 字体名映射到可用 TTF。
func defaultSHXMap() map[string]string {
	return map[string]string{
		"gbcbig.shx":   "default",
		"hztxt.shx":    "default",
		"hzfs.shx":     "default",
		"chineset.shx": "default",
		"st.shx":       "default",
		"tssdchn.shx":  "default",
		"tssdeng.shx":  "default",
		"@gbcbig.shx":  "default",
	}
}
