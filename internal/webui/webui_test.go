package webui

import (
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/huanxiang666/dwg2pdf/internal/pipeline"
)

const miniDXF = "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n11\n100\n21\n100\n0\nLINE\n8\n0\n10\n200\n20\n200\n11\n300\n21\n300\n0\nENDSEC\n0\nEOF\n"

func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	conv := pipeline.NewConverter(nil)
	return httptest.NewServer(Handler(conv, "test"))
}

func postMultipart(t *testing.T, url string, fields map[string]string, fileName, content string) *http.Response {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	for k, v := range fields {
		mw.WriteField(k, v)
	}
	fw, err := mw.CreateFormFile("files", fileName)
	if err != nil {
		t.Fatal(err)
	}
	fw.Write([]byte(content))
	mw.Close()
	resp, err := http.Post(url, mw.FormDataContentType(), &buf)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func TestEnginesEndpoint(t *testing.T) {
	ts := newTestServer(t)
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/api/engines")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status %d", resp.StatusCode)
	}
	var body bytes.Buffer
	body.ReadFrom(resp.Body)
	s := body.String()
	if !strings.Contains(s, `"kind"`) || !strings.Contains(s, "libredwg") || !strings.Contains(s, "oda") {
		t.Fatalf("engines 响应不完整: %s", s)
	}
}

func TestPreviewEndpoint(t *testing.T) {
	ts := newTestServer(t)
	defer ts.Close()
	resp := postMultipart(t, ts.URL+"/api/preview", nil, "mini.dxf", miniDXF)
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status %d", resp.StatusCode)
	}
	var body bytes.Buffer
	body.ReadFrom(resp.Body)
	var decoded struct {
		OK      bool `json:"ok"`
		Preview struct {
			SVG         string `json:"svg"`
			EntityCount int    `json:"entityCount"`
		} `json:"preview"`
	}
	if err := json.Unmarshal(body.Bytes(), &decoded); err != nil {
		t.Fatal(err)
	}
	if !decoded.OK || !strings.HasPrefix(decoded.Preview.SVG, "<svg") || decoded.Preview.EntityCount != 2 {
		t.Fatalf("preview 响应异常: ok=%v entities=%d svg=%.80s", decoded.OK, decoded.Preview.EntityCount, decoded.Preview.SVG)
	}
}

func TestPreviewRejectsGarbage(t *testing.T) {
	ts := newTestServer(t)
	defer ts.Close()
	resp := postMultipart(t, ts.URL+"/api/preview", nil, "bad.dxf", "not a dxf at all")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("status %d, 期望 422", resp.StatusCode)
	}
}

func TestExportRegionEndpoint(t *testing.T) {
	ts := newTestServer(t)
	defer ts.Close()
	resp := postMultipart(t, ts.URL+"/api/export-region", map[string]string{
		"minX": "-10", "minY": "-10", "maxX": "150", "maxY": "150",
	}, "mini.dxf", miniDXF)
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		var body bytes.Buffer
		body.ReadFrom(resp.Body)
		t.Fatalf("status %d: %s", resp.StatusCode, body.String())
	}
	var body bytes.Buffer
	body.ReadFrom(resp.Body)
	if !strings.Contains(body.String(), "/dl?id=") {
		t.Fatalf("缺少下载链接: %s", body.String())
	}
}

func TestExportRegionBadParams(t *testing.T) {
	ts := newTestServer(t)
	defer ts.Close()
	resp := postMultipart(t, ts.URL+"/api/export-region", map[string]string{
		"minX": "abc",
	}, "mini.dxf", miniDXF)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d, 期望 400", resp.StatusCode)
	}
}

func TestConvertWithEngineAndQuality(t *testing.T) {
	ts := newTestServer(t)
	defer ts.Close()
	resp := postMultipart(t, ts.URL+"/api/convert", map[string]string{
		"engine": "auto", "quality": "high", "paper": "a4",
	}, "mini.dxf", miniDXF)
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		var body bytes.Buffer
		body.ReadFrom(resp.Body)
		t.Fatalf("status %d: %s", resp.StatusCode, body.String())
	}
	var body bytes.Buffer
	body.ReadFrom(resp.Body)
	s := body.String()
	if !strings.Contains(s, `"name":"mini.dxf"`) || !strings.Contains(s, "/dl?id=") {
		t.Fatalf("convert 响应异常: %s", s)
	}
}
