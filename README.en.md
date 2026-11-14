# Material Dock v0.5.1

[简体中文](README.md)

## Why I built this

I work in clothing design, and I'm new to programming. I started with a question from my own office. Once I hand a materials request to our coordinator, can I check where it stands myself? For a front zipper, I might need to know whether the quantity changed, when the supplier expects it to arrive, or which row in the Excel sheet that reference photo belonged to.

I brought our spreadsheets and workflows into conversations with AI and built it one iteration at a time. That's what vibe coding looked like for me. Every time I thought a version was nearly ready, trying it against our work brought up another question. Could the coordinator update a filtered list in one go? Could I put a request on hold while a project was still undecided? Seeing an embedded photo turn up beside the right material was a small thrill. Dock was starting to feel like something I'd actually open every day.

Today, designers can submit, revise, and track their requests. The materials coordinator can manage suppliers, follow up on dates, record arrivals, and export filtered data. It runs in a browser with Python and SQLite behind it.

## Feedback and contributions are welcome

I'm still learning as I go. If you hit a bug, find a step awkward, or handle a similar workflow differently, I'd love to hear from you in [Issues](https://github.com/pupprise/Material-Dock/issues). What you were trying to do and where you got stuck are enough to start. Please use redacted screenshots and sample workbooks when sharing examples.

If you'd like to change something, feel free to fork the repo, make a commit, and open a [Pull Request](https://github.com/pupprise/Material-Dock/pulls). A clearer sentence, an Excel import test, or a small improvement to a workflow is welcome. I'd enjoy hearing what led you to the change.

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
