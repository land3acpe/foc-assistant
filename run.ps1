# FOC-Assistant V4 启动脚本 (PowerShell)
$env:PYTHONIOENCODING = "utf-8"
$python = "D:\Python312\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Python not found at $python" -ForegroundColor Red
    pause
    exit 1
}

if (-not $env:DEEPSEEK_API_KEY) {
    Write-Host "====================================" -ForegroundColor Yellow
    Write-Host "API Key not set. Retrieving from registry..."
    $regKey = Get-ItemProperty -Path "HKCU:\Environment" -Name "DEEPSEEK_API_KEY" -ErrorAction SilentlyContinue
    if ($regKey -and $regKey.DEEPSEEK_API_KEY) {
        $env:DEEPSEEK_API_KEY = $regKey.DEEPSEEK_API_KEY
        Write-Host "API Key loaded from system environment." -ForegroundColor Green
    } else {
        $env:DEEPSEEK_API_KEY = Read-Host "Enter DeepSeek API Key"
        if (-not $env:DEEPSEEK_API_KEY) {
            Write-Host "No API Key provided. Exit." -ForegroundColor Red
            exit 1
        }
    }
}

if ($args.Count -gt 0) {
    & $python -X utf8 agent.py $args
} else {
    & $python -X utf8 agent.py
}
