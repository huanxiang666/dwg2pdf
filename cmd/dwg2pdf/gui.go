package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"time"

	"github.com/huanxiang666/dwg2pdf/internal/webui"
)

// runGUI 启动本地图形界面：内嵌 Web UI + 自动打开浏览器。
// 双击 exe（无参数）即进入这里，保证"打开就能用"。
func runGUI(args []string) int {
	noBrowser := false
	port := 0
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--no-browser":
			noBrowser = true
		case "--port":
			if i+1 < len(args) {
				fmt.Sscanf(args[i+1], "%d", &port)
				i++
			}
		}
	}
	if port < 0 || port > 65535 {
		fmt.Fprintln(os.Stderr, "error: invalid GUI port (expected 0..65535)")
		return 2
	}

	logDir, err := os.UserCacheDir()
	if err != nil {
		logDir = os.TempDir()
	}
	logDir = filepath.Join(logDir, "dwg2pdf", "logs")
	if err := os.MkdirAll(logDir, 0o700); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	logPath := filepath.Join(logDir, fmt.Sprintf("gui-%d.log", os.Getpid()))
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer f.Close()
	logger := log.New(f, "", log.LstdFlags)

	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		logger.Printf("listen: %v", err)
		reportGUIError("无法启动图纸转换界面：端口被占用或网络异常。\n" + err.Error() + "\n日志：" + logPath)
		return 1
	}
	defer ln.Close()
	address := "http://" + ln.Addr().String()

	var random [24]byte
	if _, err := rand.Read(random[:]); err != nil {
		logger.Print(err)
		return 1
	}
	token := hex.EncodeToString(random[:])
	launchURL := address + "/?session=" + token
	shutdown := make(chan struct{}, 1)

	srv := &http.Server{
		Handler:           guiSessionHandler(webui.Handler(conv), address, token, shutdown),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
		ErrorLog:          logger,
	}
	done := make(chan error, 1)
	go func() { done <- srv.Serve(ln) }()
	logger.Printf("started %s version=%s", address, version)

	sessionPath := filepath.Join(logDir, fmt.Sprintf("gui-%d.json", os.Getpid()))
	info, _ := json.Marshal(map[string]any{"pid": os.Getpid(), "url": launchURL, "log": logPath})
	if err := os.WriteFile(sessionPath, info, 0o600); err != nil {
		logger.Print(err)
	}
	defer os.Remove(sessionPath)

	fmt.Printf("图纸转换界面：%s\n日志：%s\n在界面右上角点击“退出程序”关闭本服务。\n", launchURL, logPath)
	if !noBrowser {
		if err := openBrowser(launchURL); err != nil {
			logger.Printf("browser: %v", err)
			reportGUIError("自动打开浏览器失败。程序仍在运行，请复制下面地址到浏览器打开：\n" + launchURL + "\n日志：" + logPath)
		}
	}

	interrupts := make(chan os.Signal, 1)
	signal.Notify(interrupts, os.Interrupt)
	defer signal.Stop(interrupts)

	select {
	case err = <-done:
		if !errors.Is(err, http.ErrServerClosed) {
			logger.Print(err)
			reportGUIError("界面服务已停止：" + err.Error() + "\n日志：" + logPath)
			return 1
		}
	case <-shutdown:
	case <-interrupts:
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		logger.Printf("shutdown: %v", err)
		_ = srv.Close()
	}
	logger.Print("stopped")
	return 0
}

// guiSessionHandler 加会话保护：只有程序自己打开的浏览器标签能调用本地接口，
// 防止任意网页借本机服务读写文件。
func guiSessionHandler(next http.Handler, address, token string, shutdown chan<- struct{}) http.Handler {
	u, _ := url.Parse(address)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Cache-Control", "no-store")
		if r.Host != u.Host {
			http.Error(w, "invalid host", http.StatusForbidden)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != address {
			http.Error(w, "invalid origin", http.StatusForbidden)
			return
		}
		if r.Method == http.MethodGet && r.URL.Path == "/" && r.URL.Query().Get("session") == token {
			http.SetCookie(w, &http.Cookie{Name: "dwg2pdf-session", Value: token, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
		c, err := r.Cookie("dwg2pdf-session")
		if err != nil || c.Value != token {
			http.Error(w, "请从程序打开的浏览器窗口进入。", http.StatusUnauthorized)
			return
		}
		if r.URL.Path == "/api/shutdown" {
			if r.Method != http.MethodPost {
				http.Error(w, "POST required", http.StatusMethodNotAllowed)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"ok":true}`))
			select {
			case shutdown <- struct{}{}:
			default:
			}
			return
		}
		next.ServeHTTP(w, r)
	})
}

func openBrowser(address string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", address)
	case "darwin":
		cmd = exec.Command("open", address)
	default:
		cmd = exec.Command("xdg-open", address)
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }()
	return nil
}

func printGUIError(message string) { fmt.Fprintln(os.Stderr, message) }
