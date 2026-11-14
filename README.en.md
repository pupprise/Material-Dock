# Material Dock v0.5.1

[简体中文](README.md)

## 🧵 When is that zipper arriving?

A designer sends a spreadsheet and still needs a few answers. Did the quantity change? When did the supplier say it would arrive? Which row did that reference photo belong to? Meanwhile, the materials coordinator has several delivery dates to record for the same project.

Material Dock grew out of those questions. It started when a clothing designer new to programming brought office spreadsheets and real workflows into conversations with AI and started vibe coding. One practical problem led to the next. Reference photos needed to follow their rows, delivery dates needed a batch update, and undecided requests needed somewhere to wait. Seeing an embedded photo land beside the right zipper felt like a small win. :)

Today, designers can submit, revise, and track their requests. The materials coordinator can manage suppliers, follow up on dates, record arrivals, and export filtered data. It runs in a browser with Python and SQLite behind it.

## 👀 See the workspace

![Designer workspace with a zipper search and its quantity, measurements, dates, and reference photo in the details panel](docs/screenshots/02-designer-board.webp)

| Task | Start here |
| --- | --- |
| Find a request and check quantities, dates, and photos | [Board, filters, and details](docs/user-guide.en.md#board-filters-and-details) |
| Submit, import, change, or put a request on hold | [Designer requests](docs/user-guide.en.md#designer-requests) |
| Follow up in bulk, order for someone else, or record a delivery | [Coordinator workflow](docs/user-guide.en.md#coordinator-workflow) |
| Manage or select suppliers | [Supplier directory](docs/user-guide.en.md#supplier-directory) |
| Understand colors, icons, and unread badges | [Status colors and icons](docs/user-guide.en.md#status-colors-and-icons) · [Notifications and the floating Dock](docs/user-guide.en.md#notifications-and-the-floating-dock) |
| Give colleagues outside the LAN access | [Public deployment route](docs/deployment.en.md) |

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

Use `manage.py init` and then `server.py` for a fresh deployment. See [deployment](docs/deployment.en.md) for the HTTPS route and verification steps. Keep local configuration, generated invites, credentials, and runtime data outside version control.

The [Chinese documentation index](README.md#各板块文档) covers accounts, the workspace, Excel and files, the supplier directory, operations, verification and frontend assets. The application UI and workbook templates use Simplified Chinese.

## 💬 Issues and contributions are welcome

Where did you get stuck? How does your team handle the same job? Share what you expected and what happened in [Issues](https://github.com/pupprise/Material-Dock/issues). Please use redacted screenshots and sample workbooks.

If you want to make a change, fork the repo, make a commit, and open a [Pull Request](https://github.com/pupprise/Material-Dock/pulls). Clearer instructions, an import regression test, and small workflow fixes are all welcome. A sentence about the problem your change solves goes a long way.

## License

The project code and documentation are available under the [MIT License](LICENSE). Bundled Tabler icons retain their [original license and copyright notice](web/icons/LICENSE.txt).
