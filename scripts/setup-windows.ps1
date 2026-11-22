param([switch]$Dev)

. (Join-Path $PSScriptRoot 'windows-environment.ps1')
$paths = Get-FinanceWindowsPaths
Set-FinanceWindowsEnvironment $paths
Set-Location -LiteralPath $paths.Root
if (Test-FinanceWindowsEnvironment $paths -Dev:$Dev) {
    Write-Host 'Project-local Windows environment is ready.'
    exit 0
}

$lock = $null
try {
    $lock = [IO.File]::Open((Join-Path $paths.Runtime 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    if (Test-FinanceWindowsEnvironment $paths -Dev:$Dev) { exit 0 }
    $wasDev = $false
    $oldRoot = ''
    if (Test-Path -LiteralPath $paths.Stamp) {
        try {
            $old = Get-Content -LiteralPath $paths.Stamp -Raw -Encoding UTF8 | ConvertFrom-Json
            $wasDev = [bool]$old.dev
            $oldRoot = [string]$old.root
        } catch { }
        Remove-Item -LiteralPath $paths.Stamp
    }
    $installDev = $Dev -or $wasDev
    $uvVersion = '0.13.9'
    $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    switch ($architecture) {
        'X64' { $target = 'x86_64-pc-windows-msvc'; $expectedHash = 'edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb' }
        'Arm64' { $target = 'aarch64-pc-windows-msvc'; $expectedHash = '72eed595983a4d7d22d79f21065b0f4479cc7e5c7a89104bb410c8f47559a878' }
        default { throw "Unsupported Windows architecture: $architecture" }
    }
    $versionFile = Join-Path $paths.Runtime 'tools\uv-version.txt'
    if (!(Test-Path -LiteralPath $paths.Uv) -or !(Test-Path -LiteralPath $versionFile) -or
        (Get-Content -LiteralPath $versionFile -Raw).Trim() -ne $uvVersion) {
        Write-Host "Downloading project-local uv $uvVersion..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        $archive = Join-Path $paths.Runtime 'tmp\uv.zip'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-$target.zip" -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            throw 'uv download checksum mismatch. Delete .runtime\windows\tmp\uv.zip and retry.'
        }
        $tools = Join-Path $paths.Runtime 'tools'
        Expand-Archive -LiteralPath $archive -DestinationPath $tools -Force
        if (!(Test-Path -LiteralPath $paths.Uv)) { throw 'uv.exe is missing from the verified archive.' }
        [IO.File]::WriteAllText($versionFile, $uvVersion)
    }
    Write-Host 'Installing Python 3.12 inside this project (no registry or PATH changes)...'
    & $paths.Uv python install 3.12 --no-registry --no-bin
    if ($LASTEXITCODE -ne 0) { throw 'Project-local Python installation failed.' }
    # A virtual environment contains absolute interpreter paths. Rebuild after moving the project.
    if ((Test-Path -LiteralPath $paths.Venv) -and ($oldRoot -ne $paths.Root -or !(Test-Path -LiteralPath $paths.Python))) {
        Remove-Item -LiteralPath $paths.Venv -Recurse -Force
    }
    if (!(Test-Path -LiteralPath $paths.Python)) {
        & $paths.Uv venv --python 3.12 $paths.Venv
        if ($LASTEXITCODE -ne 0) { throw 'Windows virtual environment creation failed.' }
    }
    $requirements = if ($installDev) { 'requirements-dev.txt' } else { 'requirements.txt' }
    Write-Host "Installing $requirements into .venv-win..."
    & $paths.Uv pip install --python $paths.Python --only-binary :all: -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Fix the network error and run this script again.' }
    & $paths.Python -I -c "import sys, pathlib, fastapi, uvicorn, openpyxl; from zoneinfo import ZoneInfo; root=pathlib.Path(sys.argv[1]).resolve(); assert pathlib.Path(sys.prefix).resolve()==root/'.venv-win'; assert pathlib.Path(sys.base_prefix).resolve().is_relative_to(root/'.runtime'/'windows'/'python'); assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in sys.path if p); assert ZoneInfo('Asia/Shanghai'); print('Verified: Python and dependencies are inside the project.')" $paths.Root
    if ($LASTEXITCODE -ne 0) { throw 'Project-local environment verification failed.' }
    $stamp = @{ root = $paths.Root; signature = Get-FinanceEnvironmentSignature $paths; dev = [bool]$installDev }
    $temporaryStamp = $paths.Stamp + '.tmp'
    [IO.File]::WriteAllText($temporaryStamp, ($stamp | ConvertTo-Json))
    Move-Item -LiteralPath $temporaryStamp -Destination $paths.Stamp -Force
    Write-Host 'Windows setup complete. Run start-windows.cmd to start the ledger.'
} catch {
    Write-Host ("Windows setup failed: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
