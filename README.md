# Material Dock v0.5.1

[English](README.en.md)

供设计师和材料员共同使用的面辅料工作台。设计师提报、调整并查看自己的需求，材料员集中管理供应商、跟进交期、登记到料和导出。应用由 Python 提供 HTTP 服务，使用浏览器和 SQLite，无前端构建步骤。

## 本地试用

需要 Python 3.12。Windows 可在仓库根目录运行以下 PowerShell 命令。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe demo.py designer
```

macOS 或 Linux 使用 `python3.12 -m venv .venv` 创建环境，将后续命令中的 Python 路径换成 `.venv/bin/python`。

第一次启动自动生成独立演示库和随机初始密码，密码保存在 `demo-data/样例1-登录密码.txt`。三个演示入口共用样例数据。

| 入口 | 命令参数 | 账号 |
| --- | --- | --- |
| `01-设计师演示.cmd` | `demo.py designer` | 样例1 |
| `02-材料员演示.cmd` | `demo.py materials` | 样例3 |
| `03-另一位设计师.cmd` | `demo.py other` | 样例2 |

同时对照两端时使用独立浏览器配置或隐私窗口。给上述命令加 `--no-browser` 可只启动服务，适合自动化验收。演示监听本机回环地址。关闭演示可用进程管理器终止 `demo-data/server.pid` 指定的进程，先核对其命令行为本目录的 `demo.py serve`。

## 各板块文档

| 板块 | 说明 |
| --- | --- |
| 登录、角色、账号初始化 | [账号与权限](docs/accounts.md) |
| 设计师需求、材料员跟单、叠签、搜索、到料 | [工作台与业务状态](docs/workspace.md) |
| Excel、内嵌参考图、附件、导出 | [工作簿与文件](docs/workbooks.md) |
| 供应商模板、预览、去重与需求关联 | [供应商库](docs/suppliers.md) |
| 服务入口、局域网、HTTPS、备份和更新 | [部署与运维](docs/deployment.md) |
| 测试命令、入口和隐私验收 | [验收清单](docs/verification.md) |
| 原生界面模块与图标许可 | [前端说明](web/README.md) |

启动自有数据的实例使用 `manage.py init` 后运行 `server.py`。具体命令和数据位置见部署文档。
