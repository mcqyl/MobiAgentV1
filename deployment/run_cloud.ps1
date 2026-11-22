param([string]$Device = "")

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$configPath = Join-Path $repoRoot "cloud-config.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Create cloud-config.json from cloud-config.example.json and fill in api_key."
}
try {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw "cloud-config.json is not valid JSON. Check its formatting."
}
foreach ($field in @("api_key", "base_url", "model")) {
    if ($config.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($config.$field)) {
        throw "Fill in '$field' in cloud-config.json."
    }
}
$env:SUB2API_API_KEY = $config.api_key
$networkArgs = @()
if ($null -ne $config.use_env_proxy -and $config.use_env_proxy -isnot [bool]) {
    throw "use_env_proxy in cloud-config.json must be true or false."
}
if ($config.use_env_proxy -eq $true) { $networkArgs = @("--use_env_proxy") }
$wireApi = "chat_completions"
if ($config.wire_api) { $wireApi = $config.wire_api }
if ($wireApi -notin @("chat_completions", "responses")) {
    throw "wire_api in cloud-config.json must be chat_completions or responses."
}
$memoryArgs = @()
if ($null -ne $config.memory_enabled -and $config.memory_enabled -isnot [bool]) {
    throw "memory_enabled in cloud-config.json must be true or false."
}
if ($config.memory_enabled -ne $false) {
    $memoryDirectory = Join-Path $repoRoot "memory"
    if ($config.memory_dir) {
        if ($config.memory_dir -isnot [string]) { throw "memory_dir must be a directory path." }
        $memoryDirectory = $config.memory_dir
        if (-not [System.IO.Path]::IsPathRooted($memoryDirectory)) {
            $memoryDirectory = Join-Path $repoRoot $memoryDirectory
        }
    }
    $memoryArgs = @("--memory_dir", $memoryDirectory)
    if ($config.default_navigation_app) {
        if ($config.default_navigation_app -isnot [string]) { throw "default_navigation_app must be an app name." }
        $memoryArgs += @("--default_navigation_app", $config.default_navigation_app)
    }
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    python -m venv (Join-Path $repoRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
}

& $python -c "import fastapi, uvicorn, openai, PIL"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $PSScriptRoot "cloud-requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the cloud server dependencies."
    }
}

$adb = Join-Path $repoRoot ".local\adb\adb.exe"
if (Test-Path $adb) {
    if (-not $Device) {
        $devices = @(& $adb devices | ForEach-Object {
            if ($_ -match '^(\S+)\s+device\s*$') { $Matches[1] }
        })
        if ($devices.Count -ne 1) {
            throw "Connect exactly one Android device, or pass -Device <serial>."
        }
        $Device = $devices[0]
    }
    & $adb -s $Device reverse tcp:2333 tcp:2333
    if ($LASTEXITCODE -ne 0) { throw "Failed to forward the Android server port." }
    Write-Host "Android device: $Device"
}

Write-Host "App server: 127.0.0.1:2333; model: $($config.model); API: $wireApi"
& $python (Join-Path $PSScriptRoot "server.py") --host 127.0.0.1 --port 2333 --base_url $config.base_url --model $config.model --wire_api $wireApi @memoryArgs @networkArgs
exit $LASTEXITCODE
