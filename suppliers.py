import io
import re
import sqlite3
import unicodedata
import uuid
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from excel_import import ExcelIssue, guard_archive


FIELDS = {
    "name": ("供应商名称", 60), "alias": ("简称", 60), "categories": ("主营物料", 160),
    "contact": ("联系人", 60), "phone": ("电话", 80), "email": ("邮箱", 120),
    "address": ("地址", 200), "note": ("备注", 500),
}
MAX_ROWS = 500
MAX_FILE = 5 * 1024 * 1024
SEARCH_SPACE = re.compile(r"[\s\ufeff]+")
SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id TEXT PRIMARY KEY,
    name_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, alias TEXT NOT NULL, categories TEXT NOT NULL,
    contact TEXT NOT NULL, phone TEXT NOT NULL, email TEXT NOT NULL,
    address TEXT NOT NULL, note TEXT NOT NULL
);
"""


class SupplierIssue(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def normalized(value):
    return unicodedata.normalize("NFKC", value).lower()


def compact(value):
    return SEARCH_SPACE.sub("", normalized(value))


def terms_match(query, fields):
    searchable = " ".join(compact(value or "") for value in fields)
    return all(term in searchable for term in SEARCH_SPACE.split(normalized(query)))


def clean(values):
    if not isinstance(values, dict):
        raise SupplierIssue("供应商记录格式错误。")
    result = {}
    for key, (label, limit) in FIELDS.items():
        value = values.get(key, "")
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise SupplierIssue(f"「{label}」须为 {limit} 字以内的文本。")
        result[key] = value.strip()
    if not compact(result["name"]):
        raise SupplierIssue("请填写供应商名称。")
    return result


def save(db, raw, actor=None):
    values = clean(raw)
    identifier = raw.get("id", "") or uuid.uuid4().hex
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise SupplierIssue("供应商编号无效。")
    fields = ("name_key", *FIELDS)
    stored = (compact(values["name"]), *values.values())
    try:
        if raw.get("id"):
            fields_sql = ", ".join(field + " = ?" for field in fields)
            sql = f"UPDATE suppliers SET {fields_sql} WHERE id = ?"
            if not db.execute(sql, (*stored, identifier)).rowcount:
                raise SupplierIssue("供应商已不存在，请刷新。", 404)
        else:
            placeholders = ", ".join("?" for _ in range(len(fields) + 1))
            db.execute(f"INSERT INTO suppliers (id, {', '.join(fields)}) VALUES ({placeholders})", (identifier, *stored))
    except sqlite3.IntegrityError as error:
        raise SupplierIssue("已有同名供应商，请核对并编辑现有记录。", 409) from error
    return {"id": identifier}


def catalog(db, choices=False):
    fields = ("name", "alias", "categories") if choices else FIELDS
    return [dict(row) for row in db.execute(f"SELECT id, {', '.join(fields)} FROM suppliers ORDER BY name_key")]


def preview(db, content):
    workbook = None
    try:
        guard_archive(content)
        workbook = load_workbook(io.BytesIO(content), data_only=False, keep_links=False, read_only=True)
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()
        rows = sheet.iter_rows(min_row=1, max_col=len(FIELDS))
        headers = [str(cell.value or "").strip() for cell in next(rows)]
        if headers != [label for label, _ in FIELDS.values()]:
            raise SupplierIssue("第一行表头不匹配，请下载供应商模板重新填写。")
        known = {row["name_key"] for row in db.execute("SELECT name_key FROM suppliers")}
        seen = set()
        result = []
        for index, cells in enumerate(rows, 2):
            if all(cell.value is None for cell in cells):
                continue
            if index > MAX_ROWS + 1:
                raise SupplierIssue(f"每次最多导入 {MAX_ROWS} 条供应商。")
            raw = {key: str(cell.value).strip() if cell.value is not None else "" for key, cell in zip(FIELDS, cells)}
            issue = ""
            try:
                if any(cell.data_type in {"e", "f"} for cell in cells):
                    raise SupplierIssue("请把公式或错误值替换为文本。")
                clean(raw)
                name_key = compact(raw["name"])
                if name_key in known:
                    raise SupplierIssue("库中已存在同名供应商。")
                if name_key in seen:
                    raise SupplierIssue("表中有同名供应商。")
                seen.add(name_key)
            except SupplierIssue as error:
                issue = str(error)
            result.append({"line": index, "values": raw, "issue": issue})
        if not result:
            raise SupplierIssue("模板里还没有供应商。请从第 2 行开始填写。")
        return result
    except (ExcelIssue, BadZipFile, InvalidFileException, ValueError, TypeError, KeyError, IndexError, OSError, StopIteration, ParseError) as error:
        raise SupplierIssue("无法读取工作簿，请使用供应商 .xlsx 模板。") from error
    finally:
        if workbook:
            workbook.close()


def confirm(db, data, actor):
    rows = data.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise SupplierIssue(f"请选择 1–{MAX_ROWS} 条供应商。")
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict) or "id" in row:
                raise SupplierIssue("批量入库只接受新增供应商。")
            save(db, row)
        except SupplierIssue as error:
            raise SupplierIssue(f"所选第 {index} 条 · {error}", error.status) from error
    return {"count": len(rows)}
