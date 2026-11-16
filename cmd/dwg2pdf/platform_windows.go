//go:build windows

package main

import (
	"runtime"
	"syscall"
	"unsafe"
)

func goos() string      { return runtime.GOOS }
func goarch() string    { return runtime.GOARCH }
func goversion() string { return runtime.Version() }

// reportGUIError 在无控制台（windowsgui）时也能把错误送到用户眼前。
func reportGUIError(message string) {
	printGUIError(message)
	text, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString("dwg2pdf — 启动提示")
	syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), 0x10)
}
