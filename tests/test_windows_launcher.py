"""Native launcher contracts; execute with the project-local Windows interpreter."""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'Windows launcher integration')
class WindowsLauncherTest(unittest.TestCase):
    def powershell(self, code, **kwargs):
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-Command', '$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); '+code], cwd=ROOT, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=45, **kwargs)
        return result

    def project(self):
        directory = tempfile.TemporaryDirectory(prefix='launcher-中文 空格-', dir=ROOT/'.runtime'/'windows'/'tmp')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        shutil.copytree(ROOT/'scripts', root/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('requirements.txt', 'requirements-dev.txt'):
            shutil.copy(ROOT/name, root/name)
        return root

    def test_environment_paths_ignore_external_python_and_uv_settings(self):
        code = r'''
        $env:PYTHONPATH='C:\outside'; $env:PYTHONHOME='C:\outside'; $env:UV_CACHE_DIR='C:\outside';
        $env:UV_PYTHON='C:\outside\python.exe'; $env:PIP_TARGET='C:\outside';
        . .\scripts\windows-environment.ps1; $p=Get-FinanceWindowsPaths; Set-FinanceWindowsEnvironment $p;
        @{root=$p.Root; values=@{temp=$env:TEMP; tmp=$env:TMP; uv=$env:UV_CACHE_DIR;
          python=$env:UV_PYTHON_INSTALL_DIR; user=$env:PYTHONUSERBASE};
          registry=$env:UV_PYTHON_INSTALL_REGISTRY; bin=$env:UV_PYTHON_INSTALL_BIN;
          noUser=$env:PYTHONNOUSERSITE; outside=@($env:PYTHONPATH,$env:PYTHONHOME,$env:UV_PYTHON,$env:PIP_TARGET)} | ConvertTo-Json
        '''
        result = self.powershell(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(all(Path(value).is_relative_to(ROOT) for value in data['values'].values()))
        self.assertTrue(all(value is None for value in data['outside']))
        self.assertEqual((data['registry'], data['bin'], data['noUser']), ('0','0','1'))

    def test_ready_setup_works_offline_without_reinstalling(self):
        stamp = ROOT/'.runtime'/'windows'/'environment.json'
        if not stamp.exists():
            self.skipTest('Run setup-windows.ps1 -Dev first')
        before = stamp.read_bytes(), stamp.stat().st_mtime_ns
        result = self.powershell(r'''
        $env:HTTPS_PROXY='http://127.0.0.1:9'; $env:HTTP_PROXY=$env:HTTPS_PROXY;
        & .\scripts\setup-windows.ps1 -Dev
        ''')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('environment is ready', result.stdout)
        self.assertEqual((stamp.read_bytes(), stamp.stat().st_mtime_ns), before)

    def test_unicode_stamp_dependency_changes_and_moved_project_are_detected(self):
        root = self.project()
        helper = root/'scripts'/'windows-environment.ps1'
        code = f". '{helper}'; $p=Get-FinanceWindowsPaths; Set-FinanceWindowsEnvironment $p; " + r'''
        New-Item -ItemType Directory -Force (Split-Path $p.Python),$p.PythonRoot | Out-Null;
        [IO.File]::WriteAllText($p.Python,'placeholder');
        $s=@{root=$p.Root;signature=(Get-FinanceEnvironmentSignature $p);dev=$true};
        [IO.File]::WriteAllText($p.Stamp,($s | ConvertTo-Json));
        if (!(Test-FinanceWindowsEnvironment $p -Dev)) { throw 'Unicode stamp was not readable' };
        Add-Content -LiteralPath (Join-Path $p.Root 'requirements.txt') -Value '# changed';
        if (Test-FinanceWindowsEnvironment $p) { throw 'Changed dependency file accepted' };
        $s.signature=Get-FinanceEnvironmentSignature $p; $s.root='C:\old-project';
        [IO.File]::WriteAllText($p.Stamp,($s | ConvertTo-Json));
        if (Test-FinanceWindowsEnvironment $p) { throw 'Moved environment accepted' };
        Write-Host 'PASS'
        '''
        result = self.powershell(code)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('PASS', result.stdout)

    def test_busy_port_is_rejected_by_current_and_legacy_entries(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port=listener.getsockname()[1]
            for script in ('start-windows.ps1','start-wsl.ps1'):
                with self.subTest(script=script):
                    result=self.powershell(f'& .\\scripts\\{script} -Port {port}; exit $LASTEXITCODE')
                    self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                    self.assertIn('already in use', result.stdout)
                    self.assertNotIn('Starting the ledger', result.stdout)

    def test_failed_setup_returns_error_without_a_ready_stamp(self):
        root=self.project()
        runtime=root/'.runtime'/'windows'
        runtime.mkdir(parents=True)
        script=root/'scripts'/'setup-windows.ps1'
        lock=runtime/'setup.lock'
        result=self.powershell(f"$lock=[IO.File]::Open('{lock}','OpenOrCreate','ReadWrite','None'); "
            f"try {{ & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File '{script}'; "
            "$result=$LASTEXITCODE } finally { $lock.Dispose() }; exit $result")
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        self.assertIn('setup failed',result.stdout)
        self.assertFalse((runtime/'environment.json').exists())
