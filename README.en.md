# Material Dock v0.5.1

[简体中文](README.md)

Material Dock is a materials-request and delivery workspace. Designers submit and manage their requests. The materials coordinator manages suppliers, follows up on delivery dates, records arrivals, and exports filtered data. Python serves a browser interface and stores records in SQLite.

## Run locally

Use Python 3.12. From the repository root on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe demo.py designer
```

On macOS and Linux, use `python3.12 -m venv .venv` followed by `.venv/bin/python` in the remaining commands.

The demo binds to loopback and generates a random initial password in `demo-data/样例1-登录密码.txt`. Use `demo.py designer` for 样例1, `demo.py materials` for 样例3, and `demo.py other` for 样例2. The Windows launchers provide the same three entry points. Use separate browser profiles to view accounts simultaneously. Append `--no-browser` for scripted checks.

## Operations

Use `manage.py init` and then `server.py` for a fresh deployment. See [deployment](docs/deployment.md) for example commands, LAN and HTTPS settings, and backup steps. Keep local configuration, generated invites, credentials and runtime data outside version control.

The [Chinese documentation index](README.md#各板块文档) covers accounts, the workspace, Excel and files, the supplier directory, operations, verification and frontend assets. The application UI and workbook templates use Simplified Chinese.
