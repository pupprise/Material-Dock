import base64
from datetime import date
from io import BytesIO
from urllib.parse import urlencode

from openpyxl import Workbook, load_workbook

import test_smoke


class DirectoryTest(test_smoke.WorkspaceTest):
    def test_catalog_permissions_duplicate_and_preferred_supplier(self):
        owner = self.sign_in("Designer-A")
        materials = self.sign_in("Materials")
        self.request(owner, "/api/suppliers", status=403)
        self.request(owner, "/api/suppliers/save", {"name": "Example"}, status=403)
        entry = {"name": "  华东拉链厂  ", "alias": "HD ZIP", "categories": "反穿拉链", "phone": "001200"}
        saved = self.request(materials, "/api/suppliers/save", entry | {"operationId": "catalog-new"})
        self.assertEqual(self.request(materials, "/api/suppliers/save", entry | {"operationId": "catalog-new"}), saved)
        self.request(materials, "/api/suppliers/save", entry | {"operationId": "catalog-duplicate"}, status=409)
        list_for_owner = self.request(owner, "/api/workspace")["supplierChoices"]
        self.assertEqual(list_for_owner, [{"id": saved["id"], "name": "华东拉链厂", "alias": "HD ZIP", "categories": "反穿拉链"}])
        self.assertEqual(self.request(materials, "/api/suppliers")["suppliers"][0]["phone"], "001200")
        material = {"operationId": "chosen-vendor", "project": "春季 样衣", "style": "S-001",
                    "purpose": "打样", "name": "前门拉链", "specification": "长度 70 cm", "quantity": "10",
                    "unit": "条", "requiredBy": date.today().isoformat(), "preferredSupplier": "华东拉链厂",
                    "preferredSupplierId": saved["id"]}
        self.request(owner, "/api/items", material)
        self.assertEqual(self.request(materials, "/api/workspace")["items"][0]["preferred_supplier"], "华东拉链厂")
        query = urlencode({"q": "前门 70cm"})
        workbook = self.request(materials, "/api/export?" + query, headers={"Accept": "application/json"})
        with BytesIO(base64.b64decode(workbook["xlsx"])) as content:
            book = load_workbook(content, read_only=True)
            self.assertEqual(list(book.active.values)[1][4], "前门拉链")
            book.close()

    def test_template_download_and_empty_import_is_rejected(self):
        materials = self.sign_in("Materials")
        raw = self.request(materials, "/api/suppliers/template", raw=True)
        self.assertTrue(raw.startswith(b"PK"))
        self.request(materials, "/api/suppliers/preview", raw, {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        }, status=422)
        self.request(self.sign_in("Designer-A"), "/api/suppliers/template", status=403)

    def test_batch_preview_and_conflict_is_atomic(self):
        materials = self.sign_in("Materials")
        book = Workbook()
        sheet = book.active
        sheet.append(["供应商名称", "简称", "主营物料", "联系人", "电话", "邮箱", "地址", "备注"])
        sheet.append(["第一家", "样一", "拉链", "", "0012", "", "", ""])
        sheet.append(["第一 家", "", "", "", "", "", "", ""])
        sheet.append(["第二家", "", "", "", "", "", "", ""])
        content = BytesIO()
        book.save(content)
        rows = self.request(materials, "/api/suppliers/preview", content.getvalue(), {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        })["rows"]
        self.assertEqual([bool(row["issue"]) for row in rows], [False, True, False])
        self.assertEqual(rows[0]["values"]["phone"], "0012")
        self.request(materials, "/api/suppliers/save", rows[2]["values"] | {"operationId": "second"})
        self.request(materials, "/api/suppliers/confirm", {"rows": [rows[0]["values"], rows[2]["values"]], "operationId": "conflict"}, status=409)
        self.assertEqual([row["name"] for row in self.request(materials, "/api/suppliers")["suppliers"]], ["第二家"])
        commit = {"rows": [rows[0]["values"]], "operationId": "confirmed"}
        result = self.request(materials, "/api/suppliers/confirm", commit)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.request(materials, "/api/suppliers/confirm", commit), result)

    def test_excel_supplier_legacy_fallback_and_explicit_blank(self):
        owner = self.sign_in("Designer-A")
        common = {"project": "旧订单", "style": "S-2", "purpose": "打样", "specification": "150 g/m²",
                  "quantity": "5", "unit": "米", "requiredBy": date.today().isoformat(),
                  "source": {"file": "old.xlsx", "sheet": "供应商", "row": 2, "preferredSupplier": "原意向厂家"}}
        rows = [common | {"name": "旧客户端"}, common | {"name": "清空旧厂家", "preferredSupplier": ""},
                common | {"name": "长文本", "preferredSupplier": "甲" * 512}]
        self.request(owner, "/api/import/commit", {"rows": rows, "operationId": "legacy"})
        items = {row["name"]: row for row in self.request(owner, "/api/workspace")["items"]}
        self.assertIsNone(items["旧客户端"]["preferred_supplier"])
        self.assertEqual(items["旧客户端"]["source"]["preferredSupplier"], "原意向厂家")
        self.assertEqual(items["清空旧厂家"]["preferred_supplier"], "")
        self.assertEqual(len(items["长文本"]["preferred_supplier"]), 512)
