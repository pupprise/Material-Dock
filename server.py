import base64
import binascii
import hashlib
import ipaddress
import json
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
from accounts import SCHEMA as ACCOUNT_SCHEMA, fetch_account, key_digest, key_matches, seed_accounts, validate_roster
from credentials import password_matches, password_record
from excel_import import ExcelIssue, MAX_ITEMS, MAX_UPLOAD, preview_xlsx
from reference_photos import PHOTO_SCHEMA, PhotoIssue, RequestStream, save_photo
from workbook_export import make_workbook
import suppliers


ROOT = Path(__file__).resolve().parent
HEALTH = b"Material Dock Pilot OK\n"
RELEASE = "0.5.1"
ROLES = {"owner", "materials"}
BUSINESS_TIME = timezone(timedelta(hours=8))
MAX_IMAGE = 1024 * 1024
MAX_ATTACHMENT = 20 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_TOTAL = 60 * 1024 * 1024
DEVICE_AGE = 30 * 86400
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ITEM_COLUMNS = {
    "source_json": "TEXT",
    "requester": "TEXT NOT NULL DEFAULT ''",
    "project": "TEXT NOT NULL DEFAULT ''",
    "submitted_by": "TEXT NOT NULL DEFAULT 'owner'",
    "urgent": "INTEGER NOT NULL DEFAULT 0",
    "request_note": "TEXT NOT NULL DEFAULT ''",
    "pantone": "TEXT NOT NULL DEFAULT ''",
    "match_sample": "INTEGER NOT NULL DEFAULT 0",
    "vector": "INTEGER NOT NULL DEFAULT 0",
    "owner_user": "TEXT",
    "submitted_by_user": "TEXT",
    "batch_id": "TEXT",
    "lifecycle": "TEXT NOT NULL DEFAULT 'active'",
    "preferred_supplier": "TEXT",
    "preferred_supplier_id": "TEXT",
}
ITEM_QUERY = """
SELECT items.*, (EXISTS(SELECT 1 FROM item_images WHERE item_id = items.id)
 OR EXISTS(SELECT 1 FROM request_photos WHERE item_id = items.id)) AS has_reference
FROM items ORDER BY required_by, created_at DESC, items.id
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, style TEXT NOT NULL, purpose TEXT NOT NULL, name TEXT NOT NULL,
    specification TEXT NOT NULL, color TEXT NOT NULL, amount INTEGER NOT NULL, unit TEXT NOT NULL,
    required_by TEXT NOT NULL, supplier TEXT NOT NULL DEFAULT '', eta TEXT,
    received INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
    source_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), kind TEXT NOT NULL,
    message TEXT NOT NULL, recipient TEXT, created_at TEXT NOT NULL, read_at TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    role TEXT NOT NULL, id TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT NOT NULL,
    PRIMARY KEY (role, id)
);
CREATE TABLE IF NOT EXISTS item_images (
    item_id TEXT PRIMARY KEY REFERENCES items(id), content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY, uploader TEXT NOT NULL REFERENCES accounts(name),
    item_id TEXT REFERENCES items(id), filename TEXT NOT NULL, size INTEGER NOT NULL,
    content BLOB NOT NULL, created_at INTEGER NOT NULL
);
"""


class Problem(Exception):
    def __init__(self, status, message, code=None):
        self.status = status
        self.code = code
        super().__init__(message)

    def payload(self):
        return {"error": str(self), **({"code": self.code} if self.code else {})}


def now():
    return datetime.now(BUSINESS_TIME).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text_field(data, key, label, limit, required=True):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise Problem(422, f"{label}格式不正确。")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise Problem(422, f"请填写{label}，最多 {limit} 字。")
    return value


def date_field(data, key, label):
    value = text_field(data, key, label, 10)
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise Problem(422, f"请选择有效的{label}。") from error


def quantity(data, label):
    value = text_field(data, "quantity", label, 16)
    if not re.fullmatch(r"\d{1,8}(?:\.\d{1,4})?", value, re.ASCII):
        raise Problem(422, f"{label}最多 8 位整数和 4 位小数。")
    try:
        amount = int(Decimal(value) * 10000)
    except InvalidOperation as error:
        raise Problem(422, f"请填写有效的{label}。") from error
    if amount <= 0:
        raise Problem(422, f"{label}须大于零。")
    return amount


def display_amount(amount):
    integer, fraction = divmod(amount, 10000)
    return f"{integer}.{fraction:04}".rstrip("0").rstrip(".")


def event(db, item_id, kind, message, recipient=None, important=False):
    recipient_user = None
    if recipient == "materials":
        recipient_user = db.execute("SELECT name FROM accounts WHERE role = 'materials'").fetchone()[0]
    elif recipient == "owner":
        recipient_user = db.execute("SELECT owner_user FROM items WHERE id = ?", (item_id,)).fetchone()[0]
    db.execute(
        "INSERT INTO events(item_id, kind, message, recipient, created_at, important, recipient_user) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, kind, message, recipient, now(), int(important), recipient_user),
    )


def urgent_note(note):
    affirmative = re.sub(r"加急\s*[:：]\s*(?:否|无|不加急)|(?:无需|不必|不要|暂不|不需|不用|非|无须|取消|不)\s*加急", "", note)
    return "加急" in affirmative


def checkbox(data, field, label):
    value = data.get(field, False)
    if type(value) is not bool:
        raise Problem(422, f"{label}格式不正确。")
    return value


def reference_image(data):
    value = data.get("referenceImage")
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 4 * ((MAX_IMAGE + 2) // 3):
        raise Problem(422, "参考样图片过大，请选用一张清晰的较小图片。")
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise Problem(422, "参考样图片无法读取。") from error
    if not 4 <= len(content) <= MAX_IMAGE or not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
        raise Problem(422, "参考样图片无效，请重新选择。")
    return content


def item_for_write(db, data, actor):
    item_id = text_field(data, "id", "物料编号", 36)
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None or (actor["role"] == "owner" and item["owner_user"] != actor["name"]):
        raise Problem(404, "找不到这条物料。")
    if type(data.get("version")) is not int or data["version"] != item["version"]:
        raise Problem(409, "这条物料有新的更新。请关闭窗口，刷新详情后重试。")
    return item


def attach_pending(db, data, item_id, actor):
    identifiers = data.get("attachmentIds", [])
    if not isinstance(identifiers, list) or len(identifiers) > MAX_ATTACHMENTS or any(
        not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key) for key in identifiers
    ) or len(set(identifiers)) != len(identifiers):
        raise Problem(422, "矢量图附件列表无效。")
    total = 0
    for identifier in identifiers:
        row = db.execute("SELECT size, uploader, item_id, created_at FROM attachments WHERE id = ?", (identifier,)).fetchone()
        if not row or row["uploader"] != actor["name"] or row["item_id"] is not None or row["created_at"] < time.time() - 86400:
            raise Problem(422, "有附件已失效，请重新选择。")
        total += row["size"]
    if total > MAX_ATTACHMENT_TOTAL:
        raise Problem(422, "一条需求的附件总量最多 60 MB。")
    db.executemany("UPDATE attachments SET item_id = ? WHERE id = ?", ((item_id, identifier) for identifier in identifiers))
    return bool(identifiers)


def is_designer(db, name):
    return db.execute("SELECT 1 FROM accounts WHERE name = ? AND role = 'owner'", (name,)).fetchone() is not None


def link_photos(db, identifiers, item_id, actor):
    if (not isinstance(identifiers, list)
            or any(not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key) for key in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise Problem(422, "参考图标识无效。请重新导入或上传。")
    for index, identifier in enumerate(identifiers):
        row = db.execute("SELECT hash, mime FROM photo_uploads WHERE id = ? AND owner = ?", (identifier, actor["name"])).fetchone()
        if not row:
            raise Problem(422, "参考图无法读取。请重新导入或上传。")
        db.execute("INSERT INTO request_photos VALUES (?, ?, ?, ?)", (item_id, index, *row))


def create_item(db, data, actor, batch_id=None):
    role = actor["role"]
    if role == "owner":
        owner_user = requester = actor["name"]
    else:
        owner_user = text_field(data, "ownerUser", "归属设计师", 40, required=False)
        if owner_user and not is_designer(db, owner_user):
            raise Problem(422, "请选择有效的归属设计师。")
        requester = owner_user or text_field(data, "requester", "其他部门需求人", 40)
    project = text_field(data, "project", "项目", 80)
    style = text_field(data, "style", "款号", 40)
    purpose = text_field(data, "purpose", "用途", 8)
    if purpose not in {"打样", "大货", "补单"}:
        raise Problem(422, "请选择打样、大货或补单。")
    name = text_field(data, "name", "物料名", 60)
    specification = text_field(data, "specification", "规格", 160, required=False)
    color = text_field(data, "color", "颜色", 60, required=False)
    amount = quantity(data, "需求数量")
    unit = text_field(data, "unit", "单位", 12)
    required_by = date_field(data, "requiredBy", "需求到料日")
    source = data.get("source")
    default_note = source.get("requestNote", "") if isinstance(source, dict) else ""
    request_note = text_field({"note": data.get("note", default_note)}, "note", "需求备注", 512, required=False)
    urgent = checkbox(data, "urgent", "加急") or urgent_note(request_note)
    pantone = text_field(data, "pantone", "潘通色号", 60, required=False)
    match_sample = checkbox(data, "matchSample", "顺料样色")
    image = reference_image(data)
    if source is not None:
        if not isinstance(source, dict) or type(source.get("row")) is not int or not 1 <= source["row"] <= 5000:
            raise Problem(422, "Excel 来源行号无效。")
        source = {key: text_field(source, key, label, limit, required)
                  for key, label, limit, required in (
                      ("sheet", "工作表", 31, True), ("file", "文件名", 255, True),
                      ("rawDate", "原日期", 512, False), ("rawQuantity", "原数量", 512, False),
                      ("preferredSupplier", "意向供应商", 512, False), ("requestNote", "原备注", 512, False),
                  )} | {"row": source["row"]}
        source = json.dumps(source, ensure_ascii=False)
    item_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO items(id, style, purpose, name, specification, color, amount, unit, required_by, created_at,
        source_json, requester, project, submitted_by, urgent, request_note, pantone, match_sample, owner_user, submitted_by_user)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (item_id, style, purpose, name, specification, color, amount, unit, required_by, now(),
         source, requester, project, role, int(urgent), request_note, pantone, int(match_sample), owner_user or None, actor["name"]),
    )
    if image:
        db.execute("INSERT INTO item_images VALUES (?, ?)", (item_id, image))
    preferred, preferred_id = desired_supplier(db, data)
    db.execute("UPDATE items SET batch_id = ?, preferred_supplier = ?, preferred_supplier_id = ? WHERE id = ?",
               (batch_id or item_id, preferred, preferred_id, item_id))
    link_photos(db, data.get("photoIds", []), item_id, actor)
    if attach_pending(db, data, item_id, actor):
        db.execute("UPDATE items SET vector = 1 WHERE id = ?", (item_id,))
    message = f"{project} · 负责人 {requester} · {style} · {name} · {display_amount(amount)} {unit}"
    event(db, item_id, "加急需求" if urgent else "已提交需求", message, "materials", urgent)
    if role == "materials" and owner_user:
        event(db, item_id, "材料员代下单", message, "owner", urgent)
    return {"id": item_id}


def desired_supplier(db, data):
    name = text_field(data, "preferredSupplier", "意向供应商", 512 if data.get("source") else 60,
                      required=False) if "preferredSupplier" in data else None
    identifier = text_field(data, "preferredSupplierId", "供应商编号", 32, required=False)
    if not identifier:
        return name, None
    record = db.execute("SELECT name FROM suppliers WHERE id = ?", (identifier,)).fetchone()
    if not record:
        raise Problem(422, "供应商已不可用，请重新选择。")
    return record["name"], identifier


def supplier_of(item):
    preferred = item["preferred_supplier"]
    if preferred is None:
        preferred = (item["source"] or {}).get("preferredSupplier")
    return item["supplier"] or preferred or ""


def matches_query(item, query):
    return suppliers.terms_match(query, (
        item["name"], item["project"], item["style"], item["specification"], item["color"], item["requester"],
        supplier_of(item), item["preferred_supplier"], item["request_note"], item["state"],
    ))


def create_batch(db, data, actor):
    rows = data.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ITEMS:
        raise Problem(422, "请勾选 1 到 1,000 条物料。")
    ids = []
    batch_id = uuid.uuid4().hex
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise Problem(422, f"第 {position} 条物料格式不正确。")
        try:
            ids.append(create_item(db, row, actor, batch_id)["id"])
        except Problem as error:
            source = row.get("source")
            where = f"{source.get('sheet', '')} 第 {source.get('row', position)} 行" if isinstance(source, dict) else f"第 {position} 条"
            raise Problem(error.status, f"{where}：{error}") from error
    return {"id": ids[0], "count": len(ids)}


def set_lifecycle(db, data, actor):
    item = item_for_write(db, data, actor)
    action = data.get("change")
    transitions = {
        "hold": ({"active"}, "held", "已搁置"),
        "resume": ({"held", "withdrawn"}, "active", "已重新激活"),
        "withdraw": ({"active", "held"}, "withdrawn", "已撤销"),
        "void": ({"held", "withdrawn"}, "void", "已作废"),
    }
    if not isinstance(action, str) or action not in transitions:
        raise Problem(422, "请选择有效操作。")
    permitted, lifecycle, title = transitions[action]
    if item["lifecycle"] not in permitted or item["received"]:
        raise Problem(409, "当前状态无法执行该操作。已登记实收的需求请保留记录。")
    reason = text_field(data, "reason", "原因", 250)
    db.execute("UPDATE items SET lifecycle = ?, version = version + 1 WHERE id = ?", (lifecycle, item["id"]))
    event(db, item["id"], title, f"{actor['name']} · {reason}", "materials")
    return {"id": item["id"]}


def revise_item(db, data, actor):
    item = item_for_write(db, data, actor)
    if item["lifecycle"] not in {"active", "held"} or item["received"]:
        raise Problem(409, "当前状态无法修改需求。")
    amount = quantity(data, "需求数量")
    reason = text_field(data, "reason", "修改原因", 250)
    fields = {
        "project": text_field(data, "project", "项目", 80),
        "style": text_field(data, "style", "款号", 40),
        "name": text_field(data, "name", "物料名", 60),
        "specification": text_field(data, "specification", "规格", 160, required=False),
        "color": text_field(data, "color", "颜色", 60, required=False),
        "amount": amount,
        "unit": text_field(data, "unit", "单位", 12),
        "required_by": date_field(data, "requiredBy", "需求到料日"),
        "request_note": text_field(data, "note", "备注", 512, required=False),
    }
    labels = ("项目", "款号", "物料名", "规格", "颜色", "数量", "单位", "需求到料日", "备注")
    changes = []
    for (field, value), label in zip(fields.items(), labels):
        if item[field] != value:
            before, after = (display_amount(item[field]), display_amount(value)) if field == "amount" else (item[field], value)
            changes.append(f"{label} {before or '空'} → {after or '空'}")
    if not changes:
        raise Problem(422, "请至少修改一项内容。")
    assignments = ", ".join(field + " = ?" for field in fields)
    db.execute(f"UPDATE items SET {assignments}, version = version + 1 WHERE id = ?", (*fields.values(), item["id"]))
    event(db, item["id"], "需求已修改", f"{actor['name']} · {reason} · " + "；".join(changes), "materials")
    return {"id": item["id"]}


def follow_batch(db, data, actor):
    rows = data.get("items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ITEMS or any(not isinstance(row, dict) for row in rows):
        raise Problem(422, "请选择当前筛选下的待跟进物料。")
    note = text_field(data, "note", "最新说明", 250)
    supplier = text_field(data, "supplier", "供应商", 60, required=False)
    eta = date_field(data, "eta", "预计到料日") if data.get("eta") else ""
    selected = []
    seen = set()
    for row in rows:
        item = item_for_write(db, row, actor)
        if item["id"] in seen or item["lifecycle"] != "active" or item["received"] >= item["amount"]:
            raise Problem(409, "选择包含重复、已到齐或暂停的物料，请刷新并重新勾选。")
        seen.add(item["id"])
        selected.append(item)
    for item in selected:
        db.execute("UPDATE items SET supplier = ?, eta = ?, version = version + 1 WHERE id = ?",
                   (supplier or item["supplier"], eta or item["eta"], item["id"]))
        message = " · ".join(filter(None, (supplier, f"预计 {eta}" if eta else "", note)))
        event(db, item["id"], "批量跟进", message, "owner")
    return {"id": selected[0]["id"], "count": len(selected)}


def set_priority(db, data, actor):
    item = item_for_write(db, data, actor)
    if item["lifecycle"] != "active" or item["received"] >= item["amount"]:
        raise Problem(409, "仅未到齐、跟进中的需求可调整加急。")
    urgent = checkbox(data, "urgent", "加急")
    if bool(item["urgent"]) != urgent:
        db.execute("UPDATE items SET urgent = ?, version = version + 1 WHERE id = ?", (int(urgent), item["id"]))
        event(
            db, item["id"], "标记为加急" if urgent else "取消加急",
            f"{item['project'] or '未标注项目'} · 负责人 {item['requester'] or '未填写'} · {item['name']}",
            "materials" if urgent else None, urgent,
        )
    return {"id": item["id"]}


def set_identity(db, data, actor):
    item = item_for_write(db, data, actor)
    project = text_field(data, "project", "项目", 80)
    if actor["role"] == "owner":
        requester = actor["name"]
    else:
        if item["submitted_by_user"] or item["owner_user"]:
            raise Problem(409, "仅可分配尚未归属的旧记录。")
        requester = text_field(data, "ownerUser", "归属设计师", 40)
        if not is_designer(db, requester):
            raise Problem(422, "请选择有效的归属设计师。")
    if (requester, project, requester) != (item["requester"], item["project"], item["owner_user"]):
        db.execute("UPDATE items SET requester = ?, project = ?, owner_user = ?, version = version + 1 WHERE id = ?",
                   (requester, project, requester, item["id"]))
        db.execute("UPDATE events SET recipient_user = ? WHERE item_id = ? AND recipient = 'owner' AND recipient_user IS NULL",
                   (requester, item["id"]))
        event(db, item["id"], "更新归属", f"{project} · 负责人 {requester}", "owner" if actor["role"] == "materials" else None)
    return {"id": item["id"]}


def follow_up(db, data, actor):
    item = item_for_write(db, data, actor)
    if item["lifecycle"] != "active":
        raise Problem(409, "仅跟进中的需求可更新。")
    if item["received"] >= item["amount"]:
        raise Problem(409, "这条物料已经到齐。")
    supplier = text_field(data, "supplier", "供应商", 60)
    eta = date_field(data, "eta", "预计到料日")
    note = text_field(data, "note", "跟进备注", 250, required=False)
    date_changed = eta != item["eta"]
    if item["eta"] and date_changed and not note:
        raise Problem(422, "修改预计到料日时，请填写改期原因。")
    if not date_changed and item["supplier"] == supplier and not note:
        raise Problem(422, "请修改交期、供应商，或填写跟进备注。")
    db.execute("UPDATE items SET supplier = ?, eta = ?, version = version + 1 WHERE id = ?", (supplier, eta, item["id"]))
    title = ("交期更新" if item["eta"] else "已回填交期") if date_changed else "已更新跟进"
    dates = f"{item['eta']} → {eta}" if item["eta"] and date_changed else eta
    message = f"{supplier} · 预计 {dates}" + (f" · {note}" if note else "")
    event(db, item["id"], title, message, "owner" if date_changed else None)
    return {"id": item["id"]}


def record_receipt(db, data, actor):
    item = item_for_write(db, data, actor)
    if item["lifecycle"] != "active":
        raise Problem(409, "仅跟进中的需求可登记收货。")
    amount = quantity(data, "实收数量")
    received_on = date_field(data, "receivedOn", "收货日期")
    if received_on > datetime.now(BUSINESS_TIME).date().isoformat():
        raise Problem(422, "收货日期不能晚于今天。")
    if item["received"] >= item["amount"]:
        raise Problem(409, "这条物料已经到齐。")
    total = item["received"] + amount
    note = text_field(data, "note", "收货备注", 250, required=False)
    db.execute("UPDATE items SET received = ?, version = version + 1 WHERE id = ?", (total, item["id"]))
    complete = total >= item["amount"]
    message = f"{received_on} · 实收 {display_amount(amount)} {item['unit']} · 累计 {display_amount(total)} {item['unit']}"
    if note:
        message += f" · {note}"
    event(db, item["id"], "已到齐" if complete else "部分到料", message, "owner" if complete else None)
    return {"id": item["id"]}


def item_view(item, today):
    result = dict(item)
    source = result.pop("source_json")
    result["source"] = json.loads(source) if source else None
    for field in ("urgent", "match_sample", "vector", "has_reference"):
        result[field] = bool(result[field])
    if item["received"] >= item["amount"]:
        result["state"] = "已到齐"
    elif item["received"]:
        result["state"] = "部分到料"
    else:
        result["state"] = "待到料" if item["eta"] else "待联系"
    result["risk"] = ""
    if item["lifecycle"] != "active":
        result["state"] = {"held": "已搁置", "withdrawn": "已撤销", "void": "已作废"}[item["lifecycle"]]
    elif result["state"] != "已到齐":
        if item["required_by"] < today:
            result["risk"] = "已超需求日"
        elif item["eta"] and item["eta"] > item["required_by"]:
            result["risk"] = "预计延误"
    result["amount"] = display_amount(item["amount"])
    result["received"] = display_amount(item["received"])
    return result


def sort_items(items):
    return sorted(items, key=lambda item: (not (item["urgent"] and item["state"] != "已到齐"), not bool(item["risk"])))


def matches_filter(item, name):
    return {
        "all": item["lifecycle"] in {"active", "held"},
        "urgent": item["lifecycle"] == "active" and item["urgent"] and item["state"] != "已到齐",
        "risk": bool(item["risk"]),
        "today": item["lifecycle"] == "active" and item["state"] != "已到齐" and (item["eta"] or item["required_by"]) == datetime.now(BUSINESS_TIME).date().isoformat(),
        "open": item["state"] == "待联系",
        "progress": item["state"] in {"待到料", "部分到料"},
        "done": item["state"] == "已到齐",
        "held": item["lifecycle"] == "held",
        "archived": item["lifecycle"] in {"withdrawn", "void"},
    }.get(name, False)


class DockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config, db_path):
        self.lan = ipaddress.IPv4Interface(config["interface"])
        self.peer_networks = (self.lan.network, *(ipaddress.IPv4Network(value) for value in config.get("peerNetworks", [])))
        self.origin = f"http://{self.lan.ip}:{config['port']}"
        self.origins_by_host = {urlsplit(self.origin).netloc: self.origin}
        for origin in config.get("httpsProxyOrigins", []):
            parsed = urlsplit(origin)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("httpsProxyOrigins must contain complete HTTPS origins without paths")
            self.origins_by_host[parsed.netloc] = origin
        members = config["members"]
        self.recovery_hashes = config["recoveryHashes"]
        validate_roster(members, self.recovery_hashes)
        self.designers = tuple(name for name, role in members.items() if role == "owner")
        materials = next(name for name, role in members.items() if role == "materials")
        self.initial_password_hash = config["initialPasswordHash"]
        if not re.fullmatch(r"scrypt\$[a-f0-9]{32}\$[a-f0-9]{128}", self.initial_password_hash):
            raise ValueError("Initialize the deployment with manage.py before starting the server.")
        self.db_path = db_path
        self.photos = db_path.parent / "reference-photos"
        self.attempts = {}
        self.lock = threading.Lock()
        self.import_slots = threading.BoundedSemaphore(2)
        self.upload_slots = threading.BoundedSemaphore(2)
        self.assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/owner": ("index.html", "text/html; charset=utf-8"),
            "/materials": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/supplier-ui.js": ("supplier-ui.js", "text/javascript; charset=utf-8"),
        }
        with closing(self.database()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(ACCOUNT_SCHEMA + SCHEMA + PHOTO_SCHEMA + suppliers.SCHEMA)
            with db:
                db.execute("BEGIN IMMEDIATE")
                seed_accounts(db, members, self.recovery_hashes, self.initial_password_hash)
                columns = {column["name"] for column in db.execute("PRAGMA table_info(items)")}
                for column, declaration in ITEM_COLUMNS.items():
                    if column not in columns:
                        db.execute(f"ALTER TABLE items ADD COLUMN {column} {declaration}")
                db.execute("UPDATE items SET batch_id = id WHERE batch_id IS NULL")
                event_columns = {column["name"] for column in db.execute("PRAGMA table_info(events)")}
                if "important" not in event_columns:
                    db.execute("ALTER TABLE events ADD COLUMN important INTEGER NOT NULL DEFAULT 0")
                if "recipient_user" not in event_columns:
                    db.execute("ALTER TABLE events ADD COLUMN recipient_user TEXT")
                    db.execute("""UPDATE items SET owner_user = requester WHERE owner_user IS NULL
                                  AND requester IN (SELECT name FROM accounts WHERE role = 'owner')""")
                    db.execute("UPDATE events SET recipient_user = ? WHERE recipient = 'materials'", (materials,))
                    db.execute("""UPDATE events SET recipient_user = (SELECT owner_user FROM items WHERE items.id = events.item_id)
                                  WHERE recipient = 'owner'""")
                db.execute("CREATE INDEX IF NOT EXISTS idx_events_item ON events (item_id, id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events (recipient_user, id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_items_owner ON items (owner_user)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_attachment_item ON attachments (item_id)")
        super().__init__((str(self.lan.ip), config["port"]), Handler)

    def database(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def verify_request(self, request, client_address):
        address = ipaddress.IPv4Address(client_address[0])
        return any(address in network for network in self.peer_networks)

    def attempt(self, address, verify):
        with self.lock:
            clock = time.monotonic()
            start, count = self.attempts.get(address, (clock, 0))
            if clock - start > 60:
                start, count = clock, 0
            if count >= 10:
                raise Problem(429, "尝试次数过多，请一分钟后再试。")
            try:
                result = verify()
            except Problem:
                self.attempts[address] = (start, count + 1)
                raise
            self.attempts.pop(address, None)
            return result

    def issue(self, account, old_token):
        token = secrets.token_urlsafe(32)
        clock = int(time.time())
        expiry = 8 * 3600
        with closing(self.database()) as db, db:
            db.execute("DELETE FROM account_sessions WHERE expires_at <= ? OR token_hash = ?", (clock, key_digest(old_token)))
            db.execute("INSERT INTO account_sessions VALUES (?, ?, ?, ?)",
                       (key_digest(token), account["name"], account["revision"], clock + expiry))
        return token, expiry

    def account_for(self, token):
        if not token:
            return None
        with closing(self.database()) as db:
            return db.execute("""
                SELECT accounts.* FROM account_sessions
                JOIN accounts ON accounts.name = account_sessions.name
                    AND accounts.revision = account_sessions.revision
                WHERE token_hash = ? AND expires_at > ?
            """, (key_digest(token), int(time.time()))).fetchone()

    def revoke_session(self, token):
        if token:
            with closing(self.database()) as db, db:
                db.execute("DELETE FROM account_sessions WHERE token_hash = ?", (key_digest(token),))

    def known_device(self, account, token):
        if not token:
            return False
        with closing(self.database()) as db:
            return db.execute(
                "SELECT 1 FROM account_devices WHERE token_hash = ? AND name = ? AND revision = ? AND expires_at > ?",
                (key_digest(token), account["name"], account["revision"], int(time.time())),
            ).fetchone() is not None

    def remember_device(self, account, old_token):
        token = secrets.token_urlsafe(32)
        clock = int(time.time())
        with closing(self.database()) as db, db:
            db.execute("DELETE FROM account_devices WHERE expires_at <= ? OR token_hash = ?", (clock, key_digest(old_token)))
            db.execute("INSERT INTO account_devices VALUES (?, ?, ?, ?)",
                       (key_digest(token), account["name"], account["revision"], clock + DEVICE_AGE))
        return token

    def login(self, data, address, device):
        name = text_field(data, "name", "账号姓名", 40)
        password = text_field(data, "password", "密码", 64)
        key = text_field(data, "key", "专属密钥", 96, required=False)

        def verify():
            account = fetch_account(self.database, name)
            if not account or not password_matches(password, account["password_hash"]):
                if account and not account["setup_required"] and key_matches(key, account) and password_matches(password, self.initial_password_hash):
                    raise Problem(401, "这个账号已设置个人密码。请使用个人密码，或从本专属链接点「忘记密码」。", "personal_password_required")
                raise Problem(401, "姓名或密码不正确。", "invalid_credentials")
            if account["setup_required"] and not (key_matches(key, account) or self.known_device(account, device)):
                raise Problem(401, "在新设备上使用初始密码时，请从自己的专属链接打开。", "personal_link_required")
            return account

        return self.attempt(address, verify)

    def recover(self, data, address):
        name = text_field(data, "name", "账号姓名", 40)
        key = text_field(data, "key", "专属密钥", 96)

        def verify():
            with closing(self.database()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                account = db.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()
                if not key_matches(key, account):
                    raise Problem(401, "姓名或专属密钥不正确。")
                db.execute("UPDATE accounts SET password_hash = ?, setup_required = 1, revision = revision + 1 WHERE name = ?",
                           (self.initial_password_hash, name))
                return db.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()

        return self.attempt(address, verify)

    def change_password(self, account, data, address):
        current = text_field(data, "currentPassword", "当前密码", 64)
        new = text_field(data, "newPassword", "新密码", 64)
        if len(new) < 8 or new == current or password_matches(new, self.initial_password_hash):
            raise Problem(422, "新密码至少 8 个字符，且须与当前密码和初始密码不同。")

        def verify():
            if not password_matches(current, account["password_hash"]):
                raise Problem(401, "当前密码不正确。")
            with closing(self.database()) as db, db:
                result = db.execute("""UPDATE accounts SET password_hash = ?, setup_required = 0, revision = revision + 1
                                      WHERE name = ? AND revision = ?""",
                                    (password_record(new), account["name"], account["revision"]))
                if result.rowcount != 1:
                    raise Problem(409, "账号状态已变化，请重新登录。")
                return db.execute("SELECT * FROM accounts WHERE name = ?", (account["name"],)).fetchone()

        return self.attempt(address, verify)


class Handler(BaseHTTPRequestHandler):
    server_version = "MaterialDockPilot"
    sys_version = ""

    def setup(self):
        self.request.settimeout(45)
        super().setup()

    def log_message(self, format, *args):
        pass

    def send_payload(self, status, body, content_type, cookie=None, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        if cookie is not None:
            for value in (cookie,) if isinstance(cookie, str) else cookie:
                self.send_header("Set-Cookie", value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data, cookie=None):
        self.send_payload(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", cookie)

    def cookie_token(self, name="md_pilot"):
        try:
            entry = SimpleCookie(self.headers.get("Cookie", "")).get(name)
            return entry.value if entry else ""
        except ValueError:
            return ""

    def request_body(self, content_type, maximum, allow_empty=False):
        if self.headers.get("Origin") != self.request_origin() or self.headers.get_content_type() != content_type:
            raise Problem(403, "请从试用入口操作。")
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size > maximum:
                raise Problem(413, "提交的内容过大，请拆分后重试。")
            if size < 0 or (size == 0 and not allow_empty):
                raise ValueError()
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError()
            return body
        except ValueError as error:
            raise Problem(400, "提交内容无法读取。") from error

    def request_json(self, maximum=8192):
        try:
            data = json.loads(self.request_body("application/json", maximum))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (UnicodeError, ValueError) as error:
            raise Problem(400, "表单内容无法读取。") from error

    def require_account(self):
        account = self.server.account_for(self.cookie_token())
        if not account:
            raise Problem(401, "请使用个人账号登录。", "login_required")
        return account

    def session_result(self, account):
        return {"authenticated": True, "name": account["name"], "role": account["role"]}

    def open_session(self, account, remember=False):
        token, expiry = self.server.issue(account, self.cookie_token())
        cookies = [self.session_cookie(token, expiry)]
        if remember and account["setup_required"]:
            device = self.server.remember_device(account, self.cookie_token("md_device"))
            cookies.append(self.session_cookie(device, DEVICE_AGE, "md_device"))
        self.send_json(200, self.session_result(account), cookies)

    def request_origin(self):
        host = self.headers.get("Host")
        origin = self.server.origins_by_host.get(host)
        if origin is None and host:
            with closing(self.server.database()) as db:
                row = db.execute("SELECT value FROM settings WHERE key = 'internet_origin'").fetchone()
            if row and urlsplit(row["value"]).scheme == "https" and urlsplit(row["value"]).netloc == host:
                origin = row["value"]
        if origin is None:
            raise Problem(421, "请打开试用包内的固定地址。")
        return origin

    def session_cookie(self, token, max_age, name="md_pilot"):
        secure = "; Secure" if self.request_origin().startswith("https://") else ""
        return f"{name}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def workspace(self, account):
        role = account["role"]
        user = account["name"]
        today = datetime.now(BUSINESS_TIME).date().isoformat()
        with closing(self.server.database()) as db, db:
            db.execute("BEGIN")
            query = ITEM_QUERY if role == "materials" else ITEM_QUERY.replace("FROM items ORDER", "FROM items WHERE owner_user = ? ORDER")
            items = sort_items([item_view(item, today) for item in db.execute(query, () if role == "materials" else (user,))])
            for item in items:
                item["photos"] = [row[0] for row in db.execute(
                    "SELECT position FROM request_photos WHERE item_id = ? ORDER BY position", (item["id"],))]
                item["timeline"] = [dict(row) for row in db.execute(
                    "SELECT kind, message, created_at FROM events WHERE item_id = ? ORDER BY id DESC LIMIT 20",
                    (item["id"],),
                )]
                item["attachments"] = [dict(row) for row in db.execute(
                    "SELECT id, filename, size FROM attachments WHERE item_id = ? ORDER BY created_at, rowid", (item["id"],)
                )]
            alerts = [dict(row) for row in db.execute(
                """SELECT events.id, item_id, kind, message, read_at, events.created_at, important,
                items.name, items.requester, items.project
                FROM events JOIN items ON items.id = events.item_id WHERE recipient_user = ?
                ORDER BY (read_at IS NULL) DESC, important DESC, events.id DESC LIMIT 100""", (user,)
            )]
            unread, important = db.execute(
                "SELECT count(*), COALESCE(sum(important), 0) FROM events WHERE recipient_user = ? AND read_at IS NULL", (user,)
            ).fetchone()
            choices = suppliers.catalog(db, choices=True)
        return {"role": role, "name": user, "designers": self.server.designers if role == "materials" else (),
                "today": today, "items": items, "alerts": alerts, "unread": unread, "importantUnread": important,
                "supplierChoices": choices, "release": RELEASE}

    def export_items(self, query):
        if self.require_account()["role"] != "materials":
            raise Problem(403, "请在材料员工作台导出。")
        parameters = parse_qs(query, max_num_fields=7)
        project = parameters.get("project", [""])[0]
        requester = parameters.get("requester", [""])[0]
        supplier = parameters.get("supplier", [""])[0]
        date_from = parameters.get("from", [""])[0]
        date_to = parameters.get("to", [""])[0]
        status = parameters.get("status", ["all"])[0]
        keyword = parameters.get("q", [""])[0][:100]
        today = datetime.now(BUSINESS_TIME).date().isoformat()
        with closing(self.server.database()) as db:
            items = [item_view(item, today) for item in db.execute(ITEM_QUERY)]
        filtered = [
            item for item in sort_items(items)
            if (not project or item["project"] == project)
            and (not requester or item["requester"] == requester)
            and (not supplier or supplier_of(item) == supplier)
            and (not date_from or item["created_at"][:10] >= date_from)
            and (not date_to or item["created_at"][:10] <= date_to)
            and matches_filter(item, status)
            and matches_query(item, keyword)
        ]
        content = make_workbook(filtered)
        if self.headers.get("Accept") == "application/json":
            self.send_json(200, {"filename": f"物料跟单-{today}.xlsx", "xlsx": base64.b64encode(content).decode("ascii")})
        else:
            self.send_payload(200, content, XLSX_TYPE, filename=f"material-dock-{today}.xlsx")

    def image(self, path, thumb=False):
        actor = self.require_account()
        match = re.fullmatch(r"/api/items/([a-f0-9-]{36})/reference(?:/(\d{1,5}))?", path)
        if not match:
            raise Problem(404, "找不到参考样图片。")
        item_id, position = match.groups()
        with closing(self.server.database()) as db:
            if position is not None:
                row = db.execute("""SELECT image.hash, image.mime, items.owner_user FROM request_photos AS image
                                    JOIN items ON items.id = image.item_id WHERE image.item_id = ? AND position = ?""",
                                 (item_id, position)).fetchone()
            else:
                row = db.execute("""SELECT image.content, items.owner_user FROM item_images AS image
                                    JOIN items ON items.id = image.item_id WHERE image.item_id = ?""", (item_id,)).fetchone()
        if not row or (actor["role"] == "owner" and row["owner_user"] != actor["name"]):
            raise Problem(404, "找不到参考样图片。")
        if position is not None:
            self.send_photo(row["hash"], row["mime"], thumb)
            return
        self.send_payload(200, row["content"], "image/jpeg")

    def send_photo(self, fingerprint, mime, thumb=False):
        path = self.server.photos / fingerprint
        thumbnail = path.with_suffix(".thumb")
        if thumb and thumbnail.is_file():
            path, mime = thumbnail, "image/jpeg"
        with path.open("rb") as source:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, no-cache")
            self.end_headers()
            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)

    def staged_photo(self, stream, actor):
        fingerprint, mime = save_photo(self.server.photos, stream)
        identifier = uuid.uuid4().hex
        with closing(self.server.database()) as db, db:
            db.execute("INSERT INTO photo_uploads VALUES (?, ?, ?, ?)", (identifier, actor["name"], fingerprint, mime))
        return identifier

    def upload_photo(self, actor):
        if self.headers.get("Origin") != self.request_origin() or self.headers.get_content_type() != "application/octet-stream":
            raise Problem(403, "请从 Dock 页面上传图片。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise Problem(400, "图片长度无效。") from error
        if length <= 0:
            raise Problem(400, "请选择有效图片。")
        if not self.server.upload_slots.acquire(blocking=False):
            raise Problem(503, "有附件正在上传，请稍后再试。")
        try:
            identifier = self.staged_photo(RequestStream(self.rfile, length), actor)
            self.send_json(201, {"id": identifier})
        except PhotoIssue as error:
            raise Problem(422, str(error)) from error
        finally:
            self.server.upload_slots.release()

    def preview_photo(self, actor, path, thumb=False):
        identifier = path.rsplit("/", 1)[-1]
        with closing(self.server.database()) as db:
            row = db.execute("SELECT hash, mime FROM photo_uploads WHERE id = ? AND owner = ?",
                             (identifier, actor["name"])).fetchone()
        if not row:
            raise Problem(404, "找不到预览图片。")
        self.send_photo(*row, thumb)

    def upload_attachment(self, actor):
        filename = unquote(self.headers.get("X-Dock-Filename", "")).strip()
        if not filename or len(filename) > 255 or any(ord(ch) < 32 or ch in "/\\" for ch in filename):
            raise Problem(422, "附件文件名无效。")
        if not self.server.upload_slots.acquire(blocking=False):
            raise Problem(503, "有附件正在上传，请稍后再试。")
        try:
            content = self.request_body("application/octet-stream", MAX_ATTACHMENT, allow_empty=True)
            identifier = uuid.uuid4().hex
            clock = int(time.time())
            with closing(self.server.database()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM attachments WHERE item_id IS NULL AND created_at < ?", (clock - 86400,))
                count, total = db.execute("""SELECT count(*), COALESCE(sum(size), 0) FROM attachments
                                           WHERE uploader = ? AND item_id IS NULL""", (actor["name"],)).fetchone()
                if count >= MAX_ATTACHMENTS or total + len(content) > MAX_ATTACHMENT_TOTAL:
                    raise Problem(422, "待提交的附件最多 8 个、合计 60 MB，请先移除不需要的文件。")
                db.execute("INSERT INTO attachments VALUES (?, ?, NULL, ?, ?, ?, ?)",
                           (identifier, actor["name"], filename, len(content), content, clock))
            self.send_json(201, {"id": identifier, "filename": filename, "size": len(content)})
        finally:
            self.server.upload_slots.release()

    def discard_attachments(self, actor, data):
        identifiers = data.get("ids")
        if not isinstance(identifiers, list) or len(identifiers) > MAX_ATTACHMENTS or any(not isinstance(key, str) for key in identifiers):
            raise Problem(422, "附件列表无效。")
        with closing(self.server.database()) as db, db:
            db.executemany("DELETE FROM attachments WHERE id = ? AND uploader = ? AND item_id IS NULL",
                           ((identifier, actor["name"]) for identifier in identifiers))
        self.send_json(200, {})

    def download_attachment(self, actor, path):
        match = re.fullmatch(r"/api/attachments/([a-f0-9]{32})", path)
        if not match:
            raise Problem(404, "找不到附件。")
        with closing(self.server.database()) as db:
            row = db.execute("""SELECT a.content, a.filename, items.owner_user FROM attachments AS a
                                JOIN items ON items.id = a.item_id WHERE a.id = ?""", (match[1],)).fetchone()
        if not row or (actor["role"] == "owner" and row["owner_user"] != actor["name"]):
            raise Problem(404, "找不到附件。")
        if self.headers.get("Accept") == "application/json":
            self.send_json(200, {"filename": row["filename"], "file": base64.b64encode(row["content"]).decode("ascii")})
        else:
            self.send_payload(200, row["content"], "application/octet-stream", filename=row["filename"])

    def write_operation(self, actor, path, data):
        role = actor["role"]
        actions = {
            "/api/items": (ROLES, create_item),
            "/api/import/commit": (ROLES, create_batch),
            "/api/follow-up": ({"materials"}, follow_up),
            "/api/follow-up/batch": ({"materials"}, follow_batch),
            "/api/items/revise": ({"owner"}, revise_item),
            "/api/items/lifecycle": ({"owner"}, set_lifecycle),
            "/api/receipt": ({"materials"}, record_receipt),
            "/api/priority": ({"owner"}, set_priority),
            "/api/identity": (ROLES, set_identity),
            "/api/suppliers/save": ({"materials"}, suppliers.save),
            "/api/suppliers/confirm": ({"materials"}, suppliers.confirm),
        }
        allowed_roles, apply = actions[path]
        if role not in allowed_roles:
            raise Problem(403, "当前角色无权执行此操作。")
        key = text_field(data, "operationId", "操作编号", 64)
        signature = digest(path + json.dumps(data, sort_keys=True, ensure_ascii=False))
        with closing(self.server.database()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute("SELECT payload_hash, result FROM operations WHERE role = ? AND id = ?", (actor["name"], key)).fetchone()
            if saved:
                if saved["payload_hash"] != signature:
                    raise Problem(409, "这次操作编号已被使用。请重新打开表单。")
                return json.loads(saved["result"])
            result = apply(db, data, actor)
            db.execute("INSERT INTO operations VALUES (?, ?, ?, ?)", (actor["name"], key, signature, json.dumps(result)))
            return result

    def do_GET(self):
        try:
            self.request_origin()
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in self.server.assets:
                name, content_type = self.server.assets[path]
                self.send_payload(200, (ROOT / "web" / name).read_bytes(), content_type)
            elif re.fullmatch(r"/icons/(?:needle-thread|layers-intersect|box|tag|ruler-measure|circle-dashed|zipper)\.svg", path):
                self.send_payload(200, (ROOT / "web" / path[1:]).read_bytes(), "image/svg+xml")
            elif path == "/healthz":
                self.send_payload(200, HEALTH, "text/plain; charset=utf-8")
            elif path == "/versionz":
                self.send_json(200, {"version": RELEASE})
            elif path == "/api/session":
                account = self.server.account_for(self.cookie_token())
                self.send_json(200, self.session_result(account) if account else {"authenticated": False})
            elif path == "/api/workspace":
                self.send_json(200, self.workspace(self.require_account()))
            elif path == "/api/export":
                self.export_items(parsed.query)
            elif path in {"/api/suppliers", "/api/suppliers/template"}:
                self.require_materials()
                if path.endswith("/template"):
                    content = (ROOT / "web" / "supplier-template.xlsx").read_bytes()
                    if self.headers.get("Accept") == "application/json":
                        self.send_json(200, {"filename": "供应商导入模板.xlsx", "xlsx": base64.b64encode(content).decode("ascii")})
                    else:
                        self.send_payload(200, content, XLSX_TYPE, filename="供应商导入模板.xlsx")
                else:
                    with closing(self.server.database()) as db:
                        self.send_json(200, {"suppliers": suppliers.catalog(db)})
            elif path.startswith("/api/items/"):
                self.image(path, parsed.query == "thumb=1")
            elif path.startswith("/api/photos/"):
                self.preview_photo(self.require_account(), path, parsed.query == "thumb=1")
            elif path.startswith("/api/attachments/"):
                self.download_attachment(self.require_account(), path)
            else:
                raise Problem(404, "页面不存在。")
        except Problem as error:
            self.send_json(error.status, error.payload())
        except ValueError:
            self.send_json(400, {"error": "查询参数不正确。"})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (sqlite3.Error, OSError) as error:
            print(f"GET failed: {type(error).__name__}", file=sys.stderr, flush=True)
            self.send_json(500, {"error": "暂时无法读取，请重试。"})

    def require_materials(self):
        if self.require_account()["role"] != "materials":
            raise Problem(403, "请使用材料员账号管理供应商。")

    def handle_suppliers(self, path):
        self.require_materials()
        try:
            if path.endswith("/preview"):
                if not self.server.import_slots.acquire(blocking=False):
                    raise Problem(503, "正在解析其他工作簿，请稍后重试。")
                try:
                    content = self.request_body(XLSX_TYPE, suppliers.MAX_FILE)
                    with closing(self.server.database()) as db:
                        result = {"rows": suppliers.preview(db, content)}
                finally:
                    self.server.import_slots.release()
            else:
                data = self.request_json(2 * 1024 * 1024 if path.endswith("/confirm") else 8192)
                result = self.write_operation(self.require_account(), path, data)
            self.send_json(200, result)
        except suppliers.SupplierIssue as error:
            raise Problem(error.status, str(error)) from error

    def do_POST(self):
        try:
            self.request_origin()
            path = urlsplit(self.path).path
            if path in {"/api/suppliers/save", "/api/suppliers/preview", "/api/suppliers/confirm"}:
                self.handle_suppliers(path)
                return
            if path == "/api/import/preview":
                actor = self.require_account()
                filename = unquote(self.headers.get("X-Dock-Filename", ""))
                if not filename.lower().endswith(".xlsx") or len(filename) > 255 or any(ch in filename for ch in ("/", "\\", "\x00")):
                    raise Problem(422, "请选择 .xlsx 文件。旧版 .xls 请在 Excel 中另存为 .xlsx。")
                if not self.server.import_slots.acquire(blocking=False):
                    raise Problem(503, "已有工作簿正在解析，请稍后重试。")
                try:
                    payload = self.request_body(XLSX_TYPE, MAX_UPLOAD)
                    result = preview_xlsx(payload, lambda stream: self.staged_photo(stream, actor))
                    self.send_json(200, {"file": filename, **result})
                except ExcelIssue as error:
                    raise Problem(422, str(error)) from error
                finally:
                    self.server.import_slots.release()
                return
            if path == "/api/attachments/stage":
                self.upload_attachment(self.require_account())
                return
            if path == "/api/photos/stage":
                self.upload_photo(self.require_account())
                return
            maximum = {"/api/import/commit": 8 * 1024 * 1024, "/api/items": 2 * 1024 * 1024,
                       "/api/follow-up/batch": 128 * 1024}.get(path, 8192)
            data = self.request_json(maximum)
            if path == "/api/login":
                remember = checkbox(data, "rememberDevice", "记住设备")
                self.open_session(self.server.login(data, self.client_address[0], self.cookie_token("md_device")), remember)
                return
            if path == "/api/recover":
                remember = checkbox(data, "rememberDevice", "记住设备")
                self.open_session(self.server.recover(data, self.client_address[0]), remember)
                return
            if path == "/api/password":
                actor = self.require_account()
                self.open_session(self.server.change_password(actor, data, self.client_address[0]))
                return
            if path == "/api/logout":
                self.server.revoke_session(self.cookie_token())
                self.send_json(200, {}, self.session_cookie("", 0))
                return
            actor = self.require_account()
            if path == "/api/read":
                if type(data.get("id")) is not int:
                    raise Problem(422, "通知编号无效。")
                with closing(self.server.database()) as db, db:
                    db.execute("UPDATE events SET read_at = ? WHERE id = ? AND recipient_user = ? AND read_at IS NULL", (now(), data["id"], actor["name"]))
                self.send_json(200, {})
            elif path == "/api/attachments/discard":
                self.discard_attachments(actor, data)
            elif path in {"/api/items", "/api/import/commit", "/api/follow-up", "/api/follow-up/batch",
                          "/api/items/revise", "/api/items/lifecycle", "/api/receipt", "/api/priority", "/api/identity"}:
                self.send_json(200, self.write_operation(actor, path, data))
            else:
                raise Problem(404, "操作不存在。")
        except Problem as error:
            self.send_json(error.status, error.payload())
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (sqlite3.Error, OSError) as error:
            print(f"POST failed: {type(error).__name__}", file=sys.stderr, flush=True)
            self.send_json(503, {"error": "保存暂时失败，请用当前表单重试。"})


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    store = ROOT / config["storagePath"]
    store.mkdir(parents=True, exist_ok=True)
    with DockServer(config, store / "dock.sqlite3") as server:
        print(f"Material Dock Pilot running at {server.origin}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
