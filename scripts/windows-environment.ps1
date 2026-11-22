Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-FinanceWindowsPaths {
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $runtime = Join-Path $root '.runtime\windows'
    [PSCustomObject]@{
        Root = $root
        Runtime = $runtime
        Uv = Join-Path $runtime 'tools\uv.exe'
        PythonRoot = Join-Path $runtime 'python'
        Venv = Join-Path $root '.venv-win'
        Python = Join-Path $root '.venv-win\Scripts\python.exe'
        Stamp = Join-Path $runtime 'environment.json'
    }
}

function Set-FinanceWindowsEnvironment($Paths) {
    # Only this process and its children are changed. Never persist environment variables.
    Get-ChildItem Env: | Where-Object { $_.Name -like 'UV_*' -or $_.Name -like 'PIP_*' -or $_.Name -like 'PYTHON*' } |
        ForEach-Object { Remove-Item ('Env:' + $_.Name) }
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    $values = @{
        UV_NO_CONFIG = '1'
        UV_NO_MODIFY_PATH = '1'
        UV_PYTHON_PREFERENCE = 'only-managed'
        UV_PYTHON_NO_REGISTRY = 'true'
        UV_PYTHON_INSTALL_REGISTRY = '0'
        UV_PYTHON_INSTALL_BIN = '0'
        UV_PYTHON_INSTALL_DIR = $Paths.PythonRoot
        UV_PYTHON_BIN_DIR = Join-Path $Paths.Runtime 'bin'
        UV_PYTHON_CACHE_DIR = Join-Path $Paths.Runtime 'python-cache'
        UV_CACHE_DIR = Join-Path $Paths.Runtime 'cache'
        UV_TOOL_DIR = Join-Path $Paths.Runtime 'tool-envs'
        UV_TOOL_BIN_DIR = Join-Path $Paths.Runtime 'tool-bin'
        PIP_CONFIG_FILE = 'NUL'
        PIP_CACHE_DIR = Join-Path $Paths.Runtime 'pip-cache'
        PYTHONNOUSERSITE = '1'
        PYTHONUSERBASE = Join-Path $Paths.Runtime 'user'
        PYTHONUTF8 = '1'
        TEMP = Join-Path $Paths.Runtime 'tmp'
        TMP = Join-Path $Paths.Runtime 'tmp'
        TMPDIR = Join-Path $Paths.Runtime 'tmp'
    }
    foreach ($directory in @($Paths.Runtime, $values.TEMP)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    foreach ($entry in $values.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
}

function Get-FinanceEnvironmentSignature($Paths) {
    $files = @('requirements.txt', 'requirements-dev.txt', 'scripts\setup-windows.ps1', 'scripts\windows-environment.ps1')
    return (($files | ForEach-Object { (Get-FileHash -LiteralPath (Join-Path $Paths.Root $_) -Algorithm SHA256).Hash }) -join ':')
}

function Test-FinanceWindowsEnvironment($Paths, [switch]$Dev) {
    if (!(Test-Path -LiteralPath $Paths.Python) -or !(Test-Path -LiteralPath $Paths.Stamp)) { return $false }
    try {
        $stamp = Get-Content -LiteralPath $Paths.Stamp -Raw -Encoding UTF8 | ConvertFrom-Json
        return $stamp.root -eq $Paths.Root -and $stamp.signature -eq (Get-FinanceEnvironmentSignature $Paths) -and
            (!$Dev -or $stamp.dev) -and (Test-Path -LiteralPath $Paths.PythonRoot)
    } catch { return $false }
}
