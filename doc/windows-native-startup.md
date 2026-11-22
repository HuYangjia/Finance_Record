# Windows 原生启动与项目内环境

设计日期：2026-11-22。

## 现状与目标

原入口 `scripts/start-wsl.cmd` 调用 WSL 中的 `.venv/bin/python`。现在改为 Windows 原生 Python，直接使用同一项目的 `data/finance.db`、`backups/`、`exports/` 和静态资源；不搬迁或复制正式账本，不改金额、预算、冲销及账户余额规则。

用户要求运行环境只放在项目文件夹。Windows 自带 PowerShell 负责准备及启动；下载独立 uv 可执行文件，再用它安装项目专属 CPython 3.12 和 Windows 虚拟环境。不调用系统 Python，不使用全局 pip、不注册 Python、不写用户或系统 PATH、PowerShell 配置或系统环境变量。uv 的官方说明允许指定 [存储位置和注册行为](https://docs.astral.sh/uv/reference/environment/)；下载采用 [官方独立发布包](https://docs.astral.sh/uv/getting-started/installation/#github-releases)，固定版本并校验 SHA256。

## 目录与启动流程

- `.runtime/windows/`：uv、托管 Python、下载／依赖缓存、临时目录和安装完成标记；全部忽略提交。
- `.venv-win/`：Windows 专属虚拟环境，与原 WSL `.venv/` 分开。虚拟环境只引用项目内 Python。
- `启动记账.cmd`、`scripts/start-windows.cmd` 为默认入口；旧的 `start-wsl.cmd/.ps1` 转向 Windows 新入口，已有快捷方式仍可使用且不再唤醒 WSL。
- 首次运行自动准备环境，失败显示原因、返回非零状态且不启动服务；中途失败不会写入“完成”标记。后续运行仅核对本地依赖签名和解释器，环境正常时直接启动 Uvicorn，不重复下载或安装。
- `scripts/setup-windows.ps1` 支持单独准备环境以及 `-Dev` 安装测试依赖。依赖或安装脚本变化后，下次启动重新准备；已经安装的开发依赖不会因普通启动被清除。项目整体移动后，重新建立 `.venv-win/`，不引用旧位置或退回系统 Python。
- Python、uv 的缓存、临时文件及工具目录全部显式指向项目；Python 禁用用户 site-packages、忽略外部 `PYTHONPATH` / `PYTHONHOME`。外部 uv/pip 配置不参与安装，包仅安装到显式指定的 `.venv-win`；关闭 uv 的 Python 注册及全局可执行链接安装。
- Windows 通常没有 IANA 系统时区库，依赖增加仅 Windows 安装的 `tzdata`，使既有 `ZoneInfo('Asia/Shanghai')` 正常工作。
- 服务继续仅监听 `127.0.0.1:8000`，不启用热重载。端口占用时提示先停止原服务，不能误将另一服务当作本次启动成功；不用同时从 Windows 与 WSL 写同一 SQLite 文件。
- 服务保持前台运行，关闭窗口或 Ctrl+C 停止。首次联网安装与日常启动耗时分别说明，后台汇率同步及 Excel 延迟加载继续生效。

## 验证

在 Windows PowerShell 5.1 下验证中文路径、首次安装、已安装重复执行、无网络的日常启动和依赖变化重建。检查 Python 路径、基础解释器、site-packages、uv 安装与缓存／临时目录均在项目内，用户与系统 PATH／Python 注册项保持原状。先使用项目目录下的隔离账本验证服务健康检查、记账／还款、预算、冲销、定存和 Excel 导出，运行 Windows 后端回归；不得用正式账本做测试写入。检查端口冲突、准备失败和退出码，验证旧入口调用新 Windows 入口。完成后提交代码和文档，环境、账本和日志保持忽略。

首轮 Windows 回归发现部分旧测试用 `with sqlite3.connect(...)` 检查迁移备份，只提交／回滚而不关闭连接；Linux 可删除仍打开的临时文件，Windows 则阻止清理。测试改用项目已有的 `database.open_connection`，使检查结束时明确关闭；不放宽完整性断言、不忽略清理失败，应用账务行为不变。

## 实现与验证结果

本机已在项目内准备 uv 0.13.9、CPython 3.12.16 和依赖。uv 的 x64 / ARM64 发布包校验值固定在准备脚本中。安装后核对用户 PATH、系统 PATH 与 Python 注册项，均保持原状；解释器、基础 Python、模块路径、临时目录均在项目内。`AGENTS.md` 同步记录此约束，避免以后维护时安装到全局。

Windows 原生完整回归 182 项通过（84.731 秒，含 5 项启动专项）；专项覆盖外部 Python / uv / pip 环境隔离、离线复用且不重写完成标记、中文及空格路径、依赖签名与移动目录检测、当前和旧入口端口冲突、安装锁失败不留下完成标记。部分早期测试使用未关闭连接的问题已修复。

`tests/windows_service_smoke.py` 使用项目内全新隔离副本，实际调用 Windows 启动脚本：健康检查、首页、收入分配、信用卡消费及还款、预算余额和 Excel 均通过。已有环境的服务启动到健康检查可响应约 2.799 秒（包括 PowerShell，不含首次下载），这是本机当次观测；后台汇率使用隔离快照，正式账本未作测试写入。运行方式为先执行 `setup-windows.ps1 -Dev`，再从同一项目环境运行 `.venv-win\Scripts\python.exe tests/windows_service_smoke.py`。

根目录 `启动记账.cmd` 与旧 `start-wsl.cmd` 在端口被占用时均正确显示原因并以非零状态退出。Linux / WSL 回归 177 项通过（118.450 秒），5 项 Windows 专项按平台跳过，原开发环境仍可使用。正式账本未搬迁，运行环境和测试产物均被 Git 忽略。
