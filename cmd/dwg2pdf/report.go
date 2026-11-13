package main

import (
	"encoding/json"
	"os"
)

type reportFile struct {
	Tool    string       `json:"tool"`
	Totals  reportTotals `json:"totals"`
	Results []reportOne  `json:"results"`
}

type reportTotals struct {
	Files   int     `json:"files"`
	OK      int     `json:"ok"`
	Failed  int     `json:"failed"`
	Seconds float64 `json:"seconds"`
}

type reportOne struct {
	Source  string  `json:"source"`
	Target  string  `json:"target"`
	Status  string  `json:"status"`
	Error   string  `json:"error,omitempty"`
	Seconds float64 `json:"seconds"`
}

// writeReport 写出 JSON 报告。
func writeReport(path string, results []convResult, el float64, okN, failN int) error {
	rf := reportFile{
		Tool:    "dwg2pdf " + version,
		Totals:  reportTotals{Files: len(results), OK: okN, Failed: failN, Seconds: el},
		Results: make([]reportOne, 0, len(results)),
	}
	for _, r := range results {
		one := reportOne{Source: r.src, Target: r.dst, Seconds: r.sec, Status: "ok"}
		if r.err != nil {
			one.Status = "failed"
			one.Error = r.err.Error()
		}
		rf.Results = append(rf.Results, one)
	}
	b, err := json.MarshalIndent(rf, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o644)
}
