import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


COLUMNS = (
    ("项目", 25), ("负责人", 16), ("款号", 18), ("用途", 10), ("物料名称", 25), ("规格", 25), ("颜色", 16),
    ("需求数量", 13), ("单位", 9), ("实收数量", 13), ("需求到料日", 15), ("预计到料日", 15), ("供应商", 22),
    ("状态", 12), ("交期风险", 14), ("优先级", 10), ("潘通色号", 16), ("顺料样色", 12), ("提供矢量图", 12),
    ("参考样", 19), ("需求备注", 40), ("提报方式", 17), ("下单人", 17), ("来源", 26),
)


def rows(items):
    for item in items:
        source = item["source"] or {}
        source_label = f"{source['file']} · {source['sheet']}!{source['row']}" if source else ""
        yield (
            item["project"], item["requester"], item["style"], item["purpose"], item["name"], item["specification"], item["color"],
            Decimal(item["amount"]), item["unit"], Decimal(item["received"]),
            date.fromisoformat(item["required_by"]), date.fromisoformat(item["eta"]) if item["eta"] else None,
            item["supplier"] or (item["preferred_supplier"] if item.get("preferred_supplier") is not None else source.get("preferredSupplier", "")),
            item["state"], item["risk"], "加急" if item["urgent"] else "",
            item["pantone"], "是" if item["match_sample"] else "", "是" if item["vector"] else "",
            "有 · 在 Dock 查看" if item["has_reference"] else "", item["request_note"] or source.get("requestNote", ""),
            "材料员代下单" if item["submitted_by"] == "materials" else "设计师提报", item["submitted_by_user"] or "", source_label,
        )


def make_workbook(items):
    book = Workbook()
    sheet = book.active
    sheet.title = "物料跟单"
    sheet.freeze_panes = "A2"
    for column, (title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(1, column, title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4844A4")
        sheet.column_dimensions[get_column_letter(column)].width = width
    for row_index, row in enumerate(rows(items), start=2):
        for column, value in enumerate(row, start=1):
            cell = sheet.cell(row_index, column, value)
            if isinstance(value, str):
                cell.data_type = "s"
            if isinstance(value, date):
                cell.number_format = "yyyy-mm-dd"
    sheet.auto_filter.ref = sheet.dimensions
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()
