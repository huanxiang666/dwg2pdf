package dwg

import (
	"os"
	"strings"
)

// 这些小包装把 os 调用集中到一处，便于测试替换。

type fileInfo struct {
	isDir bool
	size  int64
}

func osStat(p string) (fileInfo, error) {
	st, err := os.Stat(p)
	if err != nil {
		return fileInfo{}, err
	}
	return fileInfo{isDir: st.IsDir(), size: st.Size()}, nil
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func readDir(p string) ([]os.DirEntry, error) {
	return os.ReadDir(p)
}

func envGet(k string) string { return os.Getenv(k) }

func executablePath() (string, error) { return os.Executable() }

func containsFold(s, sub string) bool {
	return strings.Contains(strings.ToLower(s), strings.ToLower(sub))
}
