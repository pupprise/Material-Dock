import json
import socket
import tempfile
import threading
import unittest
from datetime import date
from http.cookiejar import CookieJar
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from openpyxl import Workbook, load_workbook

from accounts import key_digest
from credentials import password_record
from server import DockServer, RELEASE, XLSX_TYPE


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "dock.sqlite3"
        self.roles = {"Designer-A": "owner", "Designer-B": "owner", "Materials": "materials"}
        self.password = "sample-password-for-tests"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        self.config = {
            "interface": "127.0.0.1/32",
            "port": port,
            "members": self.roles,
            "initialPasswordHash": password_record(self.password),
            "recoveryHashes": {name: key_digest(self.key(name)) for name in self.roles},
        }
        self.server = None
        self.addCleanup(self.stop)
        self.start()

    def start(self):
        self.server = DockServer(self.config, self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = None

    @staticmethod
    def key(name):
        return f"sample-key-for-{name}"

    @staticmethod
    def client():
        return build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(self, client, path, payload=None, headers=None, status=200, raw=False):
        headers = {"Origin": self.origin, "Content-Type": "application/json", **(headers or {})}
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        try:
            response = client.open(Request(self.origin + path, body, headers), timeout=3)
        except HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, status)
            content = response.read()
        return content if raw else json.loads(content)

    def sign_in(self, name):
        client = self.client()
        account = self.request(client, "/api/login", {"name": name, "password": self.password, "key": self.key(name)})
        self.assertEqual(account["name"], name)
        return client

    def test_workspace_isolation_and_session_persistence(self):
        visitor = self.client()
        self.request(visitor, "/api/workspace", status=401)
        self.request(visitor, "/api/login", {"name": "Designer-A", "password": self.password}, status=401)
        a, b, materials = (self.sign_in(name) for name in self.roles)
        attachment = self.request(a, "/api/attachments/stage", b"Sample vector", {
            "Content-Type": "application/octet-stream", "X-Dock-Filename": "sample.svg",
        }, status=201)
        entry = {
            "operationId": "smoke-request", "project": "Sample Project", "style": "S-001",
            "purpose": "打样", "name": "Test fabric", "quantity": "10", "unit": "m",
            "requiredBy": date.today().isoformat(), "note": "加急", "attachmentIds": [attachment["id"]],
            "requester": "Designer-B",
        }
        created = self.request(a, "/api/items", entry)
        self.assertEqual(self.request(a, "/api/items", entry), created)
        items = self.request(a, "/api/workspace")["items"]
        self.assertEqual([(item["id"], item["requester"]) for item in items], [(created["id"], "Designer-A")])
        self.assertEqual(self.request(b, "/api/workspace")["items"], [])
        attachment_url = "/api/attachments/" + attachment["id"]
        self.request(b, attachment_url, status=404)
        self.assertEqual(self.request(materials, attachment_url, raw=True), b"Sample vector")
        queue = self.request(materials, "/api/workspace")
        self.assertEqual(queue["importantUnread"], 1)
        self.assertEqual(queue["alerts"][0]["project"], "Sample Project")
        export = self.request(materials, "/api/export?requester=Designer-A", raw=True)
        workbook = load_workbook(BytesIO(export))
        self.assertIn("Test fabric", [value for row in workbook.active.values for value in row])
        workbook.close()
        self.request(a, "/api/export", status=403)

        self.stop()
        self.start()
        self.assertEqual(self.request(a, "/versionz")["version"], RELEASE)
        self.assertEqual(self.request(a, "/api/session")["name"], "Designer-A")
        self.request(a, "/api/logout", {})
        self.request(a, "/api/workspace", status=401)

    def test_excel_preview_and_password_reset(self):
        owner = self.sign_in("Designer-A")
        book = Workbook()
        book.active.append(["款号", "物料名称", "规格", "颜色", "数量", "单位", "需求到料日"])
        book.active.append(["S-002", "Sample trim", "45 mm", "蓝色", 12, "米", date.today()])
        content = BytesIO()
        book.save(content)
        preview = self.request(owner, "/api/import/preview", content.getvalue(), {
            "Content-Type": XLSX_TYPE, "X-Dock-Filename": "sample.xlsx",
        })
        self.assertIn("Sample trim", json.dumps(preview))
        second = self.sign_in("Designer-A")
        self.request(owner, "/api/recover", {"name": "Designer-A", "key": self.key("Designer-A")})
        self.assertEqual(self.request(second, "/api/session"), {"authenticated": False})


if __name__ == "__main__":
    unittest.main()
