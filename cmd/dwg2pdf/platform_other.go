//go:build !windows

package main

import "runtime"

func goos() string      { return runtime.GOOS }
func goarch() string    { return runtime.GOARCH }
func goversion() string { return runtime.Version() }

func reportGUIError(message string) { printGUIError(message) }
