# Material Dock v0.5.1

[English](README.en.md)

## 我为什么做这个

我是做服装设计的，也是个编程小白。设计师把面辅料需求交出去以后，能不能自己看到材料员跟到哪一步了？比如某个款的前门拉链，数量有没有改，厂家说哪天能到，附在 Excel 里的参考图还能不能对上那一行。我做 Dock 就是从这些小问题开始的。

我带着办公室用的表格和实际流程，跟 AI 一边聊一边做，这就是我做这个项目时的 vibe coding。最有意思的是，每次觉得“这版差不多能用了”，一放回工作场景里，又会冒出一个问题。比如，材料员筛完一个项目，为什么还要逐条回填？项目暂时没定，需求能不能先搁置，等确认了再继续？看到表格里的参考图真的出现在对应物料旁边时，我挺开心的。它终于开始像一个我自己也愿意每天打开的工具了。

现在的 Dock 可以让设计师提报、调整和查看自己的需求，让材料员管理供应商、跟进交期、登记到料和导出。用浏览器打开，后台是 Python 和 SQLite，安装依赖就能试。

## 欢迎提 Issue，也欢迎一起改

我还在学怎么把它做得更好。如果你试用时遇到问题、觉得某一步拧巴，或者你们办公室有更顺的做法，都欢迎在 [Issues](https://github.com/pupprise/Material-Dock/issues) 留言。告诉我你当时想完成什么、在哪一步卡住了，就已经很有帮助。截图或 Excel 请用脱敏样例。

如果你愿意动手，也欢迎 fork 后提交 commits，再开 [Pull Request](https://github.com/pupprise/Material-Dock/pulls)。修一句看不懂的说明、补个表格导入测试、改顺一个操作都欢迎。告诉我你为什么这样改，我也想看看你是怎么想的。

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
