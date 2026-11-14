import io
import re
from collections import Counter
from datetime import date, datetime
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from reference_photos import PhotoIssue, photo_type, rich_images, sheet_parts, workbook_photos, wps_images


from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


MAX_UPLOAD = 100 * 1024 * 1024
MAX_ITEMS = 1000
MAX_COLUMNS = 80
MAX_SHEET_ROWS = 5000
PURPOSES = {"打样", "大货", "补单"}
FIELD_NAMES = {
    "project": ("项目", "项目名称", "项目名", "所属项目", "项目编号", "project"),
    "requester": ("负责人", "负责人姓名", "项目负责人", "提报人", "需求人", "requester"),
    "priority": ("加急", "是否加急", "优先级", "重要程度", "urgent"),
    "style": ("款号", "货号", "款式编号", "产品款号", "款式", "stylecode", "style"),
    "purpose": ("用途", "单据用途", "需求用途", "订单用途", "purpose"),
    "name": ("材料", "材料名称", "面辅料", "面辅料名称", "辅料名称", "面料名称", "物料", "物料名称", "物料名", "品名", "name"),
    "model": ("型号", "材料型号", "物料型号", "model"),
    "specification": ("规格", "规格型号", "尺寸规格", "材料规格", "specification", "spec"),
    "color": ("颜色", "色号", "颜色色号", "颜色及色号", "color"),
    "quantity": ("备料数量", "需求数量", "申请数量", "采购数量", "数量", "quantity", "qty"),
    "unit": ("单位", "计量单位", "unit"),
    "requiredBy": ("需求到料日", "需求到料日期", "要求到料日", "需求日期", "要求到货日期", "要求到货日", "到料要求日期", "到料需求", "requiredby"),
    "ambiguousDate": ("收到日期", "到货日期", "到料日期", "交期", "日期"),
    "preferredSupplier": ("厂家", "供应商", "供应厂家", "供货厂家", "生产厂家", "意向供应商", "supplier"),
    "requestNote": ("备注", "说明", "要求", "备注说明", "note"),
    "image": ("图片", "参考图", "参考图片", "参考样", "照片", "材料图片", "物料图片", "image", "photo"),
}
STYLE_LABEL = re.compile(r"(?:产品款号|款式编号|款号|货号)\s*[:：#]?\s*([^\s，,；;、/]+)")
STYLE_ONLY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
STYLE_WITH_DIGIT = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_.-]*\d)[A-Za-z0-9][A-Za-z0-9_.-]{1,39}(?![A-Za-z0-9])")
FOOTER = re.compile(r"^(?:联系人|联系电话|收货联系人|收货电话|电话|地址|收货地址|送货地址|合计|小计|总计|制表人|审核人|审批人)(?:[：:\s].*|$)")
QUANTITY = re.compile(r"(\d{1,8}(?:\.\d{1,4})?)\s*([^\s\d.,，:：/／×xX+＋;；(（)）-]{1,12})?")
CELL_REF = re.compile(r"([A-Z]{1,3})([1-9][0-9]*)")


class ExcelIssue(Exception):
    pass


def normalize_header(value):
    return re.sub(r"[\s\u3000/／:：()（）_\-]+", "", value).lower()


HEADERS = {normalize_header(alias): name for name, aliases in FIELD_NAMES.items() for alias in aliases}


def guard_archive(content):
    with ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        xml_size = sum(item.file_size for item in entries if not item.filename.startswith("xl/media/"))
        if len(entries) > 4500 or xml_size > 48 * 1024 * 1024 or sum(item.file_size for item in entries) > 300 * 1024 * 1024:
            raise ExcelIssue("工作簿展开后过大，请删除无关工作表或拆成多个文件。")
        names = [item.filename for item in entries]
        if len(set(names)) != len(names) or "[Content_Types].xml" not in names:
            raise ExcelIssue("文件结构不完整，请用 Excel 另存为 .xlsx 后重试。")
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise ExcelIssue("请在 Excel 中另存为不含宏的 .xlsx 文件。")
        for member in entries:
            if not member.filename.lower().endswith((".xml", ".rels")):
                continue
            raw = archive.read(member)
            checked = raw.replace(b"\x00", b"").upper()
            if b"<!DOCTYPE" in checked or b"<!ENTITY" in checked:
                raise ExcelIssue("工作簿包含不支持的 XML 声明，请重新另存为 .xlsx。")
            if "/worksheets/" in member.filename and "/_rels/" not in member.filename:
                guard_sheet(raw)


def coordinates(ref):
    match = CELL_REF.fullmatch(ref)
    if not match:
        raise ExcelIssue("工作表含无效单元格坐标。")
    letters, row = match.groups()
    column = 0
    for char in letters:
        column = column * 26 + ord(char) - ord("A") + 1
    return int(row), column


def guard_sheet(raw):
    merged_cells = 0
    cells = 0
    for _, element in ElementTree.iterparse(io.BytesIO(raw)):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "c":
            cells += 1
            if cells > 50000:
                raise ExcelIssue("单张工作表单元格过多，请缩小导入范围。")
        elif tag == "mergeCell":
            first, _, last = element.attrib.get("ref", "").partition(":")
            r1, c1 = coordinates(first)
            r2, c2 = coordinates(last or first)
            merged_cells += (abs(r2 - r1) + 1) * (abs(c2 - c1) + 1)
            if merged_cells > 15000:
                raise ExcelIssue("单张工作表合并区域过大，请另存一份仅保留明细的工作表。")
        element.clear()


def cell_text(cell):
    if cell is None or cell.value is None or cell.data_type == "e":
        return ""
    value = cell.value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        if value == int(value):
            if re.fullmatch(r"0{2,16}", cell.number_format or ""):
                return str(int(value)).zfill(len(cell.number_format))
            return str(int(value))
        return str(value)
    return str(value).strip()[:512]


def map_header(texts):
    fields = {}
    for column, value in texts.items():
        field = HEADERS.get(normalize_header(value))
        if field:
            fields.setdefault(field, column)
    return fields if {"name", "quantity"} <= fields.keys() else {}


def style_marker(texts, allow_single=False):
    values = list(texts.values())
    first = values[0]
    if normalize_header(first) in ("款号", "货号", "产品款号", "款式编号") and len(values) == 2:
        return values[1].strip().strip("：:")
    match = STYLE_LABEL.match(first)
    if match and len(values) == 1:
        return match.group(1)
    if allow_single and len(values) == 1 and STYLE_ONLY.fullmatch(values[0]) and re.search(r"\d", values[0]):
        return values[0]
    return ""


def title_style(texts):
    if len(texts) != 1:
        return ""
    value = next(iter(texts.values()))
    if not re.search(r"材料|辅料|面料|下单|备料|打样|大货|补单|清单|汇总表", value):
        return ""
    candidates = STYLE_WITH_DIGIT.findall(value)
    return candidates[0] if len(candidates) == 1 else ""


def normalize_date(value):
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:\s+00:00:00)?", value)
    if not match:
        return ""
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return ""


def quantity_fields(raw, unit):
    match = QUANTITY.fullmatch(raw)
    if not match:
        return "", unit
    amount, inline_unit = match.groups()
    if inline_unit and unit and inline_unit != unit:
        return "", unit
    return amount, inline_unit or unit


def worksheet_rows(sheet):
    header = {}
    style = ""
    purpose = ""
    title_code = ""
    saw_detail = False
    anchors = {}
    for merged in sheet.merged_cells.ranges:
        if merged.min_col == merged.max_col:
            for row in range(merged.min_row + 1, min(merged.max_row, MAX_SHEET_ROWS) + 1):
                anchors[(row, merged.min_col)] = sheet.cell(merged.min_row, merged.min_col)

    for row_number, row in enumerate(
        sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, MAX_SHEET_ROWS), max_col=min(sheet.max_column, MAX_COLUMNS)),
        start=1,
    ):
        texts = {cell.column: value for cell in row if (value := cell_text(cell))}
        if not texts:
            continue
        columns = map_header(texts)
        if columns:
            header = columns
            saw_detail = False
            continue
        if FOOTER.match(next(iter(texts.values()))):
            continue
        marker = style_marker(texts, allow_single=not header)
        if not marker and not header and len(texts) == 1:
            title_code = title_style(texts) or title_code
        if marker:
            if header and saw_detail:
                purpose = ""
                title_code = ""
            style = marker
            detected = [item for item in PURPOSES if any(item in value for value in texts.values())]
            if len(detected) == 1:
                purpose = detected[0]
            continue
        if len(texts) == 1:
            heading = next(iter(texts.values()))
            indicated = [item for item in PURPOSES if item in heading]
            is_title = bool(re.search(r"下单|备料|申请单|物料清单|面辅料需求", heading))
            if is_title and (not header or saw_detail):
                header = {}
                style = ""
                title_code = title_style(texts)
                purpose = indicated[0] if len(indicated) == 1 else ""
                continue
        if not header:
            continue
        style_column = header.get("style")
        purpose_column = header.get("purpose")
        if len(texts) == 1 and next(iter(texts)) in {style_column, purpose_column}:
            continue
        if not any(texts.get(header.get(key, 0)) for key in ("name", "quantity", "specification", "model", "color")):
            continue

        def lookup(key):
            return texts.get(header.get(key), "")

        name = lookup("name")
        model = lookup("model")
        specification = lookup("specification")
        raw_quantity = lookup("quantity")
        amount, unit = quantity_fields(raw_quantity, lookup("unit"))
        required_raw = lookup("requiredBy")
        ambiguous_raw = lookup("ambiguousDate")
        line_style = lookup("style") or cell_text(anchors.get((row_number, style_column))) or style or title_code
        line_purpose = lookup("purpose") or cell_text(anchors.get((row_number, purpose_column))) or purpose
        note = lookup("requestNote")
        supplier = lookup("preferredSupplier")
        warnings = []
        if not line_style:
            warnings.append("请补款号")
        if style and title_code and style != title_code and not lookup("style"):
            warnings.append(f"标题款号 {title_code} 与独立款号不同，请确认")
        if line_purpose not in PURPOSES:
            warnings.append("请选用途")
        if not amount or not unit:
            warnings.append("请确认数量和单位")
        if not normalize_date(required_raw):
            warnings.append("请确认需求到料日")
        saw_detail = True
        yield {
            "values": {
                "project": lookup("project"),
                "requester": lookup("requester"),
                "style": line_style,
                "purpose": line_purpose if line_purpose in PURPOSES else "",
                "name": name,
                "specification": " · ".join(dict.fromkeys(filter(None, (model, specification)))),
                "color": lookup("color"),
                "quantity": amount,
                "unit": unit,
                "requiredBy": normalize_date(required_raw),
                "note": note,
                "urgent": lookup("priority").lower() in {"是", "加急", "紧急", "重要", "1", "true", "yes", "urgent"},
            },
            "source": {
                "sheet": sheet.title,
                "row": row_number,
                "rawQuantity": raw_quantity,
                "rawDate": required_raw or ambiguous_raw,
                "dateLabel": "需求到料日" if required_raw else ("收到日期等" if ambiguous_raw else ""),
                "preferredSupplier": supplier,
                "requestNote": note,
                "imageCell": lookup("image"),
            },
            "warnings": warnings,
        }


def preview_xlsx(content, stage_photo=None):
    if not content or len(content) > MAX_UPLOAD:
        raise ExcelIssue("请选择小于或等于 100 MB 的 .xlsx 文件。")
    workbook = None
    try:
        guard_archive(content)
        workbook = load_workbook(io.BytesIO(content), data_only=True, keep_links=False)
        sheets = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        if len(sheets) > 20:
            raise ExcelIssue("一次最多解析 20 个可见工作表。")
        rows = []
        with ZipFile(io.BytesIO(content)) as archive:
            parts = sheet_parts(archive)
            wps, rich = wps_images(archive), rich_images(archive)
            photo_targets = {}
            for sheet in sheets:
                if sheet.max_row > MAX_SHEET_ROWS:
                    raise ExcelIssue(f"工作表「{sheet.title}」超过 {MAX_SHEET_ROWS} 行，请拆分后重试。")
                items = list(worksheet_rows(sheet))
                for item in items:
                    source = item["source"]
                    item["source"]["expectsImage"] = bool(source.get("imageCell")) or bool(re.fullmatch(
                        r"\s*(?:有图|见图|附图|见图片|见参考图|参考图见附件|图片见附件)\s*", item["values"]["note"]))
                images = workbook_photos(archive, sheet.title, parts[sheet.title], items, wps, rich)
                for item in items:
                    targets = images.get(item["source"]["row"], [])
                    photo_targets[id(item)] = targets
                rows.extend(items)
                if len(rows) > MAX_ITEMS:
                    raise ExcelIssue("一次最多导入 1,000 条物料，请拆分文件后重试。")
            staged = {}
            for item in rows:
                targets = photo_targets[id(item)]
                item["photoCount"] = len(targets)
                item["photoIds"] = []
                for target in targets:
                    if stage_photo is None:
                        with archive.open(target) as stream:
                            photo_type(stream.read(16))
                        continue
                    if target not in staged:
                        with archive.open(target) as stream:
                            staged[target] = stage_photo(stream)
                    item["photoIds"].append(staged[target])
    except PhotoIssue as error:
        raise ExcelIssue(str(error)) from error
    except (BadZipFile, InvalidFileException, ElementTree.ParseError, KeyError, IndexError, ValueError, TypeError, OSError, RuntimeError, OverflowError, StopIteration) as error:
        raise ExcelIssue("无法读取这份工作簿，请在 Excel 中另存为有效的 .xlsx 后重试。") from error
    finally:
        if workbook is not None:
            workbook.close()
    if not rows:
        raise ExcelIssue("未找到有效明细。请确认表头有「材料／物料名称」和「备料数量／数量」。")
    counts = Counter(item["source"]["sheet"] for item in rows)
    return {"rows": rows, "sheets": list(counts)}
