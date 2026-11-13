package dxf

import "math"

// Parse 解析 DXF 字节流，提取图元。
func Parse(buf []byte) (*Drawing, error) {
	d := &Drawing{
		Blocks: make(map[string]*Block, 4096),
		Layers: make(map[string]Layer, 256),
	}
	s := NewScanner(buf)

	section := ""
	var cur Entity
	inEntity := false
	inBlockDef := false
	expectSection := false
	var curBlock *Block
	headerVar := ""
	hdrIdx := 0
	solidIdx := 0
	// HATCH 边界解析状态
	hatchInBoundary := false
	hatchHaveX := false
	hatchPendingX := 0.0
	var hatchPath []Point
	// LAYER 表解析状态
	inLayer := false
	layerName := ""
	layerColor := int32(7)

	emit := func() {
		if !inEntity {
			return
		}
		target := &d.Entities
		if inBlockDef && curBlock != nil {
			target = &curBlock.Entities
		}
		// HATCH：刷新最后一条边界路径
		if cur.Kind == KindHatch && len(hatchPath) >= 3 {
			cur.Paths = append(cur.Paths, hatchPath)
			hatchPath = nil
		}
		switch cur.Kind {
		case KindLine, KindLwPolyline:
			if len(cur.Pts) >= 2 {
				// 补齐 Bulge 长度，确保与顶点一一对应
				for len(cur.Bulge) < len(cur.Pts) {
					cur.Bulge = append(cur.Bulge, 0)
				}
				*target = append(*target, cur)
			}
		case KindCircle, KindArc, KindEllipse:
			if cur.Radius > 0 || cur.MajorA > 0 {
				// 统一圆心表示：解析器把 10/20 存进 Pts[0]，
				// 这里同步到 Center，供变换与渲染使用，避免零值漂移。
				if len(cur.Pts) > 0 {
					cur.Center = cur.Pts[0]
				} else if cur.Center.X != 0 || cur.Center.Y != 0 {
					cur.Pts = []Point{cur.Center}
				}
				*target = append(*target, cur)
			}
		case KindText:
			if cur.Text != "" {
				*target = append(*target, cur)
			}
		case KindInsert:
			if cur.BlockName != "" {
				*target = append(*target, cur)
			}
		case KindSolid, KindPoint, KindPolyline:
			if len(cur.Pts) > 0 {
				*target = append(*target, cur)
			}
		case KindHatch:
			// 填充路径存入 Paths；同时把第一圈顶点也放进 Pts 供边界统计使用
			if len(cur.Paths) > 0 {
				if len(cur.Pts) == 0 {
					cur.Pts = cur.Paths[0]
				}
				*target = append(*target, cur)
			}
		}
	}

	reset := func() {
		cur = Entity{Color: -1, XScale: 1, YScale: 1}
		inEntity = false
		solidIdx = 0
		hatchInBoundary = false
		hatchHaveX = false
		hatchPath = nil
	}

	for {
		code, val, ok := s.Next()
		if !ok {
			break
		}

		if code == 0 {
			typeName := string(val)
			emit()
			reset()

			// 结束当前 LAYER 表项并提交
			if inLayer {
				if layerName != "" {
					prev := d.Layers[layerName]
					d.Layers[layerName] = Layer{Name: layerName, Color: layerColor}
					_ = prev
				}
				inLayer = false
				layerName = ""
				layerColor = 7
			}

			switch typeName {
			case "SECTION":
				expectSection = true
			case "ENDSEC":
				section = ""
				inBlockDef = false
				curBlock = nil
			case "BLOCK":
				inBlockDef = true
				curBlock = &Block{}
			case "LAYER":
				// 开始新的 LAYER 表项
				inLayer = true
				layerName = ""
				layerColor = 7
			case "ENDBLK":
				if curBlock != nil && curBlock.Name != "" {
					if _, exists := d.Blocks[curBlock.Name]; !exists {
						d.Blocks[curBlock.Name] = curBlock
					}
				}
				inBlockDef = false
				curBlock = nil
			case "EOF":
				goto done
			default:
				if section == "ENTITIES" || inBlockDef {
					k := kindOf(typeName)
					if k != kindUnknown {
						inEntity = true
						cur.Kind = k
					}
				}
			}
			continue
		}

		// 段名
		if expectSection && code == 2 {
			section = string(val)
			expectSection = false
			continue
		}

		if code == 0 {
			continue
		}

		// HEADER 变量：$EXTMIN / $EXTMAX 记录图形真实范围
		if section == "HEADER" {
			if code == 9 {
				switch string(val) {
				case "$EXTMIN":
					headerVar = "EXTMIN"
					hdrIdx = 0
				case "$EXTMAX":
					headerVar = "EXTMAX"
					hdrIdx = 0
				default:
					headerVar = ""
				}
				continue
			}
			if headerVar != "" {
				switch code {
				case 10, 20, 30:
					v := Float(val)
					if headerVar == "EXTMIN" {
						switch code {
						case 10:
							d.HeaderMinX = v
						case 20:
							d.HeaderMinY = v
						}
					} else {
						switch code {
						case 10:
							d.HeaderMaxX = v
						case 20:
							d.HeaderMaxY = v
						}
					}
					hdrIdx++
					if hdrIdx >= 2 {
						// 至少拿到 X/Y
						if d.HeaderMaxX > d.HeaderMinX && d.HeaderMaxY > d.HeaderMinY {
							d.HasHeaderExtents = true
						}
					}
				}
				continue
			}
			continue
		}

		// 块名
		if inBlockDef && curBlock != nil && !inEntity {
			if curBlock.Name == "" && code == 2 {
				curBlock.Name = string(val)
				continue
			}
			// 块基点（10/20）
			if code == 10 {
				curBlock.BasePt.X = Float(val)
				continue
			}
			if code == 20 {
				curBlock.BasePt.Y = Float(val)
				continue
			}
		}

		// 图层表：解析 LAYER 表项的名称(2)与颜色(62)
		// 注意：必须放在 code==0 处理之后，因为 LAYER 表项从 "0/LAYER" 开始。
		if section == "TABLES" && !inEntity {
			if inLayer {
				switch code {
				case 2:
					if layerName == "" {
						layerName = string(val)
					}
				case 62:
					c := int32(Int(val))
					if c < 0 {
						c = -c
					}
					layerColor = c
				}
			}
			continue
		}

		if !inEntity {
			continue
		}

		switch code {
		case 2:
			if cur.Kind == KindInsert {
				cur.BlockName = string(val)
			}
		case 8:
			name := string(val)
			cur.LayerName = name
			cur.Layer = layerIndex(d, name)
		case 62:
			cur.Color = int32(Int(val))
		case 10:
			// 各实体的首坐标语义不同，统一收集到 Pts。
			switch cur.Kind {
			case KindSolid:
				// SOLID/3DFACE：10/11/12/13 与 20/21/22/23 配对
				ensurePts(&cur.Pts, solidIdx+1)
				cur.Pts[solidIdx].X = Float(val)
			case KindHatch:
				// HATCH 边界顶点：只在边界模式下收集，按 92 分组
				if hatchInBoundary {
					hatchPendingX = Float(val)
					hatchHaveX = true
				}
			case KindPoint:
				cur.Pts = []Point{{X: Float(val)}}
			default:
				cur.Pts = append(cur.Pts, Point{X: Float(val)})
			}
		case 20:
			if cur.Kind == KindHatch {
				if hatchInBoundary && hatchHaveX {
					hatchPath = append(hatchPath, Point{X: hatchPendingX, Y: Float(val)})
					hatchHaveX = false
				}
				break
			}
			if len(cur.Pts) > 0 {
				cur.Pts[len(cur.Pts)-1].Y = Float(val)
			}
		case 11:
			if cur.Kind == KindSolid {
				solidIdx = 1
				ensurePts(&cur.Pts, 2)
				cur.Pts[1].X = Float(val)
			} else if cur.Kind == KindLine {
				// LINE 的第二点
				ensurePts(&cur.Pts, 2)
				cur.Pts[1].X = Float(val)
			} else {
				cur.RotPr.X = Float(val)
			}
		case 21:
			if cur.Kind == KindSolid {
				ensurePts(&cur.Pts, 2)
				cur.Pts[1].Y = Float(val)
			} else if cur.Kind == KindLine {
				ensurePts(&cur.Pts, 2)
				cur.Pts[1].Y = Float(val)
			} else {
				cur.RotPr.Y = Float(val)
			}
		case 51:
			// ARC 终止角（度）
			if cur.Kind == KindArc {
				cur.EndA = Float(val) * math.Pi / 180
			}
		case 12:
			if cur.Kind == KindSolid {
				solidIdx = 2
				ensurePts(&cur.Pts, 3)
				cur.Pts[2].X = Float(val)
			}
		case 22:
			if cur.Kind == KindSolid {
				ensurePts(&cur.Pts, 3)
				cur.Pts[2].Y = Float(val)
			}
		case 13:
			if cur.Kind == KindSolid {
				solidIdx = 3
				ensurePts(&cur.Pts, 4)
				cur.Pts[3].X = Float(val)
			}
		case 23:
			if cur.Kind == KindSolid {
				ensurePts(&cur.Pts, 4)
				cur.Pts[3].Y = Float(val)
			}
		case 40:
			switch cur.Kind {
			case KindCircle, KindArc, KindEllipse:
				cur.Radius = Float(val)
				cur.MajorA = Float(val)
			case KindText:
				if cur.Height == 0 {
					cur.Height = Float(val)
				}
			case KindInsert:
				cur.XScale = Float(val)
			}
		case 41:
			switch cur.Kind {
			case KindInsert:
				cur.YScale = Float(val)
			case KindText:
				cur.Width = Float(val)
			}
		case 42:
			if cur.Kind == KindLwPolyline {
				for len(cur.Bulge) < len(cur.Pts) {
					cur.Bulge = append(cur.Bulge, 0)
				}
				if len(cur.Bulge) > 0 {
					cur.Bulge[len(cur.Pts)-1] = Float(val)
				}
			} else if cur.Kind == KindArc {
				cur.EndA = Float(val)
			}
		case 43:
			// HATCH 图案线间距
			if cur.Kind == KindHatch && cur.PatternSpacing == 0 {
				cur.PatternSpacing = Float(val)
			}
		case 44:
			if cur.Kind == KindHatch && cur.PatternSpacing == 0 {
				cur.PatternSpacing = Float(val)
			}
		case 45:
			if cur.Kind == KindHatch && cur.PatternSpacing == 0 {
				cur.PatternSpacing = Float(val)
			}
		case 52:
			// HATCH 图案角度（度）
			if cur.Kind == KindHatch {
				cur.PatternAngle = Float(val) * math.Pi / 180
			}
		case 92:
			// HATCH：开始一条新边界路径。先把上一条存入 Paths。
			if cur.Kind == KindHatch {
				if len(hatchPath) >= 3 {
					cur.Paths = append(cur.Paths, hatchPath)
				}
				hatchPath = nil
				hatchInBoundary = true
			}
		case 93:
			// HATCH：边界顶点数，从下一组 10/20 开始收集
			if cur.Kind == KindHatch {
				hatchInBoundary = true
			}
		case 97:
			// HATCH：边界路径结束标志。
			// 注意 97 在 HATCH 中可能出现多次（边界段数 + 源对象数），
			// 只在真正有顶点时提交路径，避免把已收集的路径清空。
			if cur.Kind == KindHatch {
				if len(hatchPath) >= 3 {
					cur.Paths = append(cur.Paths, hatchPath)
					hatchPath = nil
					hatchInBoundary = false
				} else if len(hatchPath) > 0 && len(cur.Paths) == 0 {
					// 点数不足，保留在 hatchPath 中等待后续顶点
				}
			}
		case 50:
			switch cur.Kind {
			case KindArc:
				cur.StartA = Float(val) * math.Pi / 180
			case KindInsert, KindText:
				cur.Rotation = Float(val) * math.Pi / 180
			}
		case 70:
			if cur.Kind == KindHatch {
				// HATCH 的 70 组码：0=图案填充, 1=实心填充
				cur.Solid = Int(val) == 1
				break
			}
			if Int(val)&1 != 0 {
				cur.Closed = true
			}
		case 1:
			if cur.Kind == KindText {
				cur.Text = string(val)
			}
		case 3:
			if cur.Kind == KindText {
				cur.Text += string(val)
			}
		}
	}
done:
	return d, nil
}

const kindUnknown EntityKind = 255

func kindOf(name string) EntityKind {
	switch name {
	case "LINE":
		return KindLine
	case "LWPOLYLINE", "POLYLINE":
		return KindLwPolyline
	case "CIRCLE":
		return KindCircle
	case "ARC":
		return KindArc
	case "ELLIPSE":
		return KindEllipse
	case "TEXT", "MTEXT", "ATTDEF", "ATTRIB":
		return KindText
	case "INSERT":
		return KindInsert
	case "SOLID", "3DFACE":
		return KindSolid
	case "HATCH":
		return KindHatch
	case "POINT":
		return KindPoint
	default:
		return kindUnknown
	}
}

// layerIndex 返回图层的稳定索引（存入 Entity.Layer）。
func layerIndex(d *Drawing, name string) int {
	if l, ok := d.Layers[name]; ok {
		return int(l.Color)
	}
	// 尚未在 LAYER 表中出现：先占位，颜色稍后可能被补全
	d.Layers[name] = Layer{Name: name, Color: 7}
	return 7
}

// ensurePts 保证切片至少有 n 个元素。
func ensurePts(p *[]Point, n int) {
	for len(*p) < n {
		*p = append(*p, Point{})
	}
}
