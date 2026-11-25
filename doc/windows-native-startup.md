# Windows 原生启动与项目内环境

## 现状与目标

默认使用 Windows 原生 Python，直接读取原 `data/finance.db`、备份、导出及静态资源。全部运行环境仅存于项目内，不使用系统 Python、全局 / 用户 pip，不修改 PATH、Python 注册表、PowerShell 配置或系统环境变量。

PowerShell 使用固定版本并校验 SHA256 的 [uv 独立发布包](https://docs.astral.sh/uv/getting-started/installation/#github-releases)，准备项目专属 CPython 3.12 与虚拟环境；[存储和注册选项](https://docs.astral.sh/uv/reference/environment/)由脚本显式设置。

## 目录与启动流程

- `.runtime/windows/` 保存 uv、托管 Python、缓存、临时文件及安装标记；`.venv-win/` 只引用项目内 Python，与 WSL `.venv/` 独立，均忽略提交。
- `启动记账.cmd`、`scripts/start-windows.cmd` 及兼容入口 `start-wsl.cmd/.ps1` 都启动 Windows，不唤醒 WSL。首次准备失败时显示原因、返回非零状态，不写完成标记或启动服务；已有有效环境直接复用。
- `setup-windows.ps1 -Dev` 安装测试依赖；普通启动保留已装开发依赖。依赖 / 准备脚本签名变化时更新，项目移动后重建虚拟环境，不引用旧位置或退回系统 Python。
- `windows-environment.ps1` 将工具、缓存和临时目录限定在项目内，禁用用户 site-packages，隔离外部 Python / uv / pip 配置，禁止写入全局注册及可执行链接。Windows 专用 `tzdata` 支持上海时区。
- 默认前台监听 `127.0.0.1:8000`，无热重载，Ctrl+C 或关闭窗口停止；端口占用即报错，不让 Windows 与 WSL 同时写同一账本。后台汇率和 Excel 延迟加载见[启动性能](startup-performance.md)。

日志显式传入 `--no-use-colors`，避免 ANSI 转义码在控制台显示为 `[32m`、`[0m`；保留启动、访问和错误日志，重启生效。

## 开发与验证

在项目目录的 PowerShell 中先准备测试环境，再加载环境约束，确保测试临时文件也留在项目内：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup-windows.ps1 -Dev
. .\scripts\windows-environment.ps1
$financePaths = Get-FinanceWindowsPaths
Set-FinanceWindowsEnvironment $financePaths
& $financePaths.Python -m unittest discover -s tests -v
& $financePaths.Python tests/windows_service_smoke.py
```

所有写入测试用隔离账本，连接通过 `database.open_connection` 明确关闭，避免 Windows 临时文件无法清理。验收覆盖中文 / 空格路径、外部环境隔离、离线复用、依赖及移动目录检测、端口冲突、安装锁失败和退出码；真实入口验证健康检查、记账还款、预算及 Excel，并强制 `UVICORN_USE_COLORS=true` 检查日志仍无 ANSI 序列。

2026-11-22 验收基线：uv 0.13.9、CPython 3.12.16；Windows 182 项通过，WSL 177 项通过、5 项平台跳过；全局 PATH / 注册项保持原状。已有环境经实际入口到健康检查约 2.8 秒（含 PowerShell、不含首次下载），仅为当次本机测量，不是固定耗时承诺。

原 WSL 环境仅供独立开发，可用 `.venv/bin/python -m scripts.init_db` 初始化，再用 `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` 启动；不得与 Windows 混用依赖或同时写正式账本。
