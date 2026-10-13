package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func main() {
	exe, err := os.Executable()
	if err != nil {
		fmt.Println("cannot resolve executable:", err)
		waitExit(1)
		return
	}
	root := filepath.Dir(exe)
	python := filepath.Join(root, "python", "python.exe")
	script := filepath.Join(root, "parser.py")

	if _, err := os.Stat(python); err != nil {
		fmt.Println("python/python.exe not found next to tg-parser.exe")
		waitExit(1)
		return
	}
	if _, err := os.Stat(script); err != nil {
		fmt.Println("parser.py not found next to tg-parser.exe")
		waitExit(1)
		return
	}

	cmd := exec.Command(python, script)
	cmd.Dir = root
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Run(); err != nil {
		fmt.Println("parser exited with error:", err)
		waitExit(1)
		return
	}
	waitExit(0)
}

func waitExit(code int) {
	fmt.Println()
	fmt.Println("Press Enter to close...")
	fmt.Scanln()
	os.Exit(code)
}
