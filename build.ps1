#!/usr/bin/env pwsh
# 本地构建 Windows 发布包
# 用法: .\build.ps1 [-Version 1.0.0]

param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "=== dwg2pdf $Version Windows x64 构建 ===" -ForegroundColor Cyan

$env:CGO_ENABLED = "0"
$env:GOOS = "windows"
$env:GOARCH = "amd64"

$dist = Join-Path $root "dist"
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Path $dist | Out-Null

Write-Host "[1/3] 编译..." -ForegroundColor Yellow
go build -trimpath -ldflags "-s -w -X main.version=$Version" -o "$dist\dwg2pdf.exe" ./cmd/dwg2pdf
if ($LASTEXITCODE -ne 0) { throw "编译失败" }

Write-Host "[2/3] 冒烟测试..." -ForegroundColor Yellow
& "$dist\dwg2pdf.exe" --version
& "$dist\dwg2pdf.exe" --check

Write-Host "[3/3] 打包..." -ForegroundColor Yellow
$name = "dwg2pdf-$Version-windows-x64"
$pkg = Join-Path $root "pkg\$name"
New-Item -ItemType Directory -Path $pkg -Force | Out-Null
Copy-Item "$dist\dwg2pdf.exe" $pkg
foreach ($f in @("README.md", "LICENSE")) {
    $src = Join-Path $root $f
    if (Test-Path $src) { Copy-Item $src $pkg }
}

$zip = Join-Path $root "pkg\$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $pkg -DestinationPath $zip

$size = [math]::Round((Get-Item $zip).Length / 1MB, 2)
Write-Host "`n完成: $zip ($size MB)" -ForegroundColor Green
Write-Host "二进制: $([math]::Round((Get-Item "$dist\dwg2pdf.exe").Length/1MB,2)) MB" -ForegroundColor Green
