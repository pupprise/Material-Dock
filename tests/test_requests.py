from datetime import date, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.drawing.image import Image

import test_smoke
from demo import sample_photo
from server import XLSX_TYPE


class RequestTest(test_smoke.WorkspaceTest):
    def test_embedded_photo_and_link_only_workbook(self):
        owner = self.sign_in("Designer-A")
        other = self.sign_in("Designer-B")
        materials = self.sign_in("Materials")
        book = Workbook()
        sheet = book.active
        sheet.append(["款号", "物料", "数量", "单位", "参考图"])
        sheet.append(["试样1", "样例1拉链", 5, "条"])
        sheet.add_image(Image(BytesIO(sample_photo())), "E2")
        workbook = BytesIO()
        book.save(workbook)
        headers = {"Content-Type": XLSX_TYPE, "X-Dock-Filename": "sample-1.xlsx"}
        result = self.request(owner, "/api/import/preview", workbook.getvalue(), headers)
        row = result["rows"][0]
        image = row["photoIds"][0]
        self.request(other, "/api/photos/" + image, status=404)
        values = row["values"] | {"project": "样例1", "purpose": "打样", "requiredBy": date.today().isoformat(),
                                  "photoIds": [image], "source": row["source"] | {"file": "样例1.xlsx"}}
        created = self.request(owner, "/api/import/commit", {"operationId": "sample-photo", "rows": [values]})
        item_url = "/api/items/" + created["id"] + "/reference/0"
        self.assertEqual(self.request(materials, item_url, raw=True), sample_photo())
        self.request(other, item_url, status=404)

        broken = Workbook()
        broken.active.append(["款号", "物料", "数量", "单位", "参考图"])
        broken.active.append(["试样2", "样例2拉链", 3, "条", "https://example.com/sample-2.jpg"])
        content = BytesIO()
        broken.save(content)
        self.request(owner, "/api/import/preview", content.getvalue(), headers, status=422)

    def test_batch_follows_and_lifecycle(self):
        owner = self.sign_in("Designer-A")
        materials = self.sign_in("Materials")
        due = (date.today() + timedelta(days=3)).isoformat()
        values = {"style": "试样1", "purpose": "打样", "project": "样例1", "unit": "条", "quantity": "12", "requiredBy": due}
        self.request(owner, "/api/import/commit", {"operationId": "round",
                     "rows": [values | {"name": name} for name in ("样例1拉链", "样例2胶条")]})
        queue = self.request(materials, "/api/workspace")["items"]
        self.assertEqual(len({item["batch_id"] for item in queue}), 1)
        versions = [{"id": item["id"], "version": item["version"]} for item in queue]
        result = self.request(materials, "/api/follow-up/batch", {"operationId": "sample-follow", "items": versions,
                              "supplier": "样例1供应商", "eta": due, "note": "样例1已确认"})
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(self.request(owner, "/api/workspace")["alerts"]), 2)
        target = versions[0]["id"]
        for action, expected in (("hold", "已搁置"), ("resume", "待到料"), ("withdraw", "已撤销"), ("void", "已作废")):
            item = next(row for row in self.request(owner, "/api/workspace")["items"] if row["id"] == target)
            self.request(owner, "/api/items/lifecycle", {"operationId": action, "id": target,
                         "version": item["version"], "change": action, "reason": "样例1"})
            updated = next(row for row in self.request(materials, "/api/workspace")["items"] if row["id"] == target)
            self.assertEqual(updated["state"], expected)
