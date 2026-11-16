package dxf

import (
	"os"
	"testing"
)

// 真实图纸验收：用环境变量 DWG2PDF_ACCEPTANCE_DXF 指向 ODA 转换出的 DXF。
// 没有该文件时跳过（CI 上不依赖外部图纸）。
//
// 断言的是"实体覆盖率"：解析+展平后，各类实体都不应为 0（历史缺陷曾把
// DIMENSION/ELLIPSE/SPLINE 整类丢掉），且展平不应产生天文坐标。
func TestRealDrawingCoverage(t *testing.T) {
	path := os.Getenv("DWG2PDF_ACCEPTANCE_DXF")
	if path == "" {
		t.Skip("set DWG2PDF_ACCEPTANCE_DXF to run real-drawing acceptance")
	}
	buf, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("cannot read %s: %v", path, err)
	}
	d, err := Parse(buf)
	if err != nil {
		t.Fatal(err)
	}
	counts := map[EntityKind]int{}
	for i := range d.Entities {
		counts[d.Entities[i].Kind]++
	}
	t.Logf("顶层实体分布: %v", counts)
	if counts[KindDimension] == 0 {
		t.Error("真实图纸应含 DIMENSION（历史缺陷：整类被丢弃）")
	}
	flat := d.Flatten()
	if len(flat) <= len(d.Entities) {
		t.Errorf("展平后实体数 %d 应大于顶层 %d（DIMENSION/INSERT 需展开）", len(flat), len(d.Entities))
	}
	// 展平结果不应有天文坐标
	for i := range flat {
		for _, p := range flat[i].Pts {
			if abs(p.X) > 1e9 || abs(p.Y) > 1e9 {
				t.Fatalf("展平产生异常坐标 (%.0f, %.0f)", p.X, p.Y)
			}
		}
	}
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}
