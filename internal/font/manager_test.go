package font

import (
	"testing"
)

func TestEmbeddedFontPresent(t *testing.T) {
	m := NewManager(Options{})
	if len(m.List()) == 0 {
		t.Fatal("内置字体未加载")
	}
	f, ok := m.Default()
	if !ok || f == nil {
		t.Fatal("取不到默认字体")
	}
	if len(f.Data) < 100000 {
		t.Errorf("字体数据异常小: %d 字节", len(f.Data))
	}
}

func TestResolveKnownNames(t *testing.T) {
	m := NewManager(Options{})
	// 各种 CAD 常见字体名都应解析到可用字体，而不是 nil
	names := []string{
		"", "simhei", "宋体", "gbcbig.shx", "hztxt.shx",
		"txt.shx", "arial", "微软雅黑", "unknown-font-xyz",
	}
	for _, n := range names {
		f, ok := m.Resolve(n)
		if !ok || f == nil {
			t.Errorf("Resolve(%q) 失败", n)
			continue
		}
		if len(f.Data) == 0 {
			t.Errorf("Resolve(%q) 返回空字体", n)
		}
	}
}

func TestGlyphMapper(t *testing.T) {
	m := NewManager(Options{})
	f, _ := m.Default()
	gm := NewGlyphMapper()
	if err := gm.Prepare(f.Name, f.Data); err != nil {
		t.Fatalf("Prepare 失败: %v", err)
	}
	// ASCII 与常见汉字必须有字形
	for _, r := range []rune{'A', '0', '中', '文', '图', '纸', '（', '）'} {
		if gid := gm.GID(f.Name, r); gid == 0 {
			t.Errorf("字形缺失: %q", r)
		}
	}
	if gm.GID(f.Name, 0x10FFFF) != 0 {
		t.Error("不存在的字符应返回 0")
	}
}

func TestAliasOverride(t *testing.T) {
	m := NewManager(Options{})
	m.SetAlias("custom.shx", "default")
	f, ok := m.Resolve("custom.shx")
	if !ok || f == nil {
		t.Fatal("别名解析失败")
	}
}

func TestGIDIsStable(t *testing.T) {
	m := NewManager(Options{})
	f, _ := m.Default()
	gm := NewGlyphMapper()
	if err := gm.Prepare(f.Name, f.Data); err != nil {
		t.Fatal(err)
	}
	a := gm.GID(f.Name, '中')
	b := gm.GID(f.Name, '中')
	if a != b || a == 0 {
		t.Errorf("字形 ID 不稳定: %d vs %d", a, b)
	}
}
