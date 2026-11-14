import hashlib
import posixpath
import re
import tempfile
from pathlib import Path
from xml.etree import ElementTree as XML
from PIL import Image, ImageOps, UnidentifiedImageError


REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
ART = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
CELL_FORMULA = re.compile(r'(?:_xlfn\.)?(?:_xlws\.)?DISPIMG\s*\(\s*"([^"]+)"', re.I)
IMAGE_FUNCTION = re.compile(r'\b(?:DISPIMG|IMAGE)\s*\(', re.I)
IMAGE_LINK = re.compile(r'(?:https?://|file:|[A-Z]:[\\/]|\\\\|www\.)|\.(?:jpe?g|png|webp|gif|bmp)(?:\s|$)', re.I)
PHOTO_SCHEMA = """
CREATE TABLE IF NOT EXISTS photo_uploads (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, hash TEXT NOT NULL, mime TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS request_photos (
    item_id TEXT NOT NULL REFERENCES items(id), position INTEGER NOT NULL,
    hash TEXT NOT NULL, mime TEXT NOT NULL, PRIMARY KEY(item_id, position)
);
"""


class PhotoIssue(ValueError):
    pass


def photo_type(header):
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"BM"):
        return "image/bmp"
    raise PhotoIssue("参考图仅支持 JPG、PNG、WebP、GIF、BMP，请检查图片格式。")


def save_photo(directory, stream):
    directory.mkdir(parents=True, exist_ok=True)
    header = stream.read(16)
    mime = photo_type(header)
    digest = hashlib.sha256(header)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(header)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                target.write(chunk)
            target.close()
            fingerprint = digest.hexdigest()
            destination = directory / fingerprint
            if not destination.exists():
                try:
                    with Image.open(temporary) as image:
                        image.verify()
                    with Image.open(temporary) as image:
                        image.draft("RGB", (256, 256))
                        image.thumbnail((256, 256))
                        thumbnail = ImageOps.exif_transpose(image).convert("RGB")
                        thumbnail.save(directory / (fingerprint + ".thumb"), "JPEG", quality=82)
                except (UnidentifiedImageError, Image.DecompressionBombError, SyntaxError, OSError) as error:
                    raise PhotoIssue("无法读取参考图，请确认图片完整且可正常打开。") from error
                temporary.replace(destination)
        finally:
            target.close()
            temporary.unlink(missing_ok=True)
    return fingerprint, mime


class RequestStream:
    def __init__(self, stream, length):
        self.stream = stream
        self.length = length

    def read(self, size):
        count = min(size, self.length)
        payload = self.stream.read(count)
        if len(payload) != count:
            raise PhotoIssue("图片传输中断，请重新选择。")
        self.length -= count
        return payload


def root(archive, path):
    return XML.fromstring(archive.read(path))


def relationships(archive, part):
    directory, filename = posixpath.split(part)
    path = posixpath.join(directory, "_rels", filename + ".rels")
    if path not in archive.namelist() and "/" + path in archive.namelist():
        path = "/" + path
    if path not in archive.namelist():
        return {}
    return {element.get("Id"): resolve_target(directory, element) for element in root(archive, path)}


def resolve_target(directory, element):
    if element.get("TargetMode", "").lower() == "external":
        return None
    target = element.get("Target", "")
    return posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(directory, target))


def media_for(archive, picture, relation):
    blip = picture.find(f".//{ART}blip")
    if blip is None or blip.get(REL + "link"):
        return None
    target = relation.get(blip.get(REL + "embed"))
    return target if target and target.startswith("xl/media/") and target in archive.namelist() else None


def sheet_parts(archive):
    part = "xl/workbook.xml"
    rels = relationships(archive, part)
    return {node.get("name"): rels[node.get(REL + "id")] for node in root(archive, part).findall(".//{*}sheet")}


def wps_images(archive):
    part = "xl/cellimages.xml"
    if part not in archive.namelist():
        return {}
    rels = relationships(archive, part)
    return {pic.find(f".//{XDR}cNvPr").get("name"): media_for(archive, pic, rels)
            for pic in root(archive, part).iter(XDR + "pic") if pic.find(f".//{XDR}cNvPr") is not None}


def rich_images(archive):
    names = {name.lower(): name for name in archive.namelist()}
    metadata = names.get("xl/metadata.xml")
    values = names.get("xl/richdata/rdrichvalue.xml")
    structures = names.get("xl/richdata/rdrichvaluestructure.xml")
    parts = names.get("xl/richdata/richvaluerel.xml")
    if not all((metadata, values, structures, parts)):
        return {}
    metadata_root = root(archive, metadata)
    types = metadata_root.find("{*}metadataTypes")
    value_metadata = metadata_root.find("{*}valueMetadata")
    future = next((part for part in metadata_root.findall("{*}futureMetadata") if part.get("name") == "XLRICHVALUE"), None)
    if types is None or value_metadata is None or future is None:
        return {}
    rels = relationships(archive, parts)
    targets = [rels.get(part.get(REL + "id")) for part in root(archive, parts).findall("{*}rel")]
    structure_nodes = list(root(archive, structures))
    rich_values = list(root(archive, values))
    pictures = {}
    for index, block in enumerate(value_metadata, start=1):
        for entry in block.findall("{*}rc"):
            if types[int(entry.get("t")) - 1].get("name") != "XLRICHVALUE":
                continue
            rich = future[int(entry.get("v"))].find(".//{*}rvb")
            if rich is None:
                continue
            value = rich_values[int(rich.get("i"))]
            structure = structure_nodes[int(value.get("s"))]
            if structure.get("t", "").lower() != "_localimage":
                continue
            position = next(i for i, key in enumerate(structure) if key.get("n") == "_rvRel:LocalImageIdentifier")
            target = targets[int(value[position].text)]
            if target and target.startswith("xl/media/") and target in names.values():
                pictures[index] = target
    return pictures


def workbook_photos(archive, sheet, part, items, wps, rich):
    by_row = {item["source"]["row"]: item for item in items}
    xml = root(archive, part)
    photos = {}

    def add(row, cell, target):
        if not target:
            raise PhotoIssue(f"「{sheet}」{cell} 的参考图片没有嵌入文件。请插入本地图片并保存后重新导入。")
        if row not in by_row:
            raise PhotoIssue(f"「{sheet}」{cell} 的图片未对应到物料行。请把图片放到相应物料行。")
        photos.setdefault(row, []).append(target)

    for row_node in xml.findall(".//{*}sheetData/{*}row"):
        row = int(row_node.get("r"))
        for cell in row_node.findall("{*}c"):
            formula = cell.findtext("{*}f", "")
            reference = cell.get("r", f"第 {row} 行")
            if match := CELL_FORMULA.search(formula):
                add(row, reference, wps.get(match.group(1)))
            elif IMAGE_FUNCTION.search(formula):
                add(row, reference, None)
            elif "HYPERLINK(" in formula.upper() and re.search(r"\.(?:jpe?g|png|webp|gif|bmp)(?:[?\"#]|$)", formula, re.I):
                add(row, reference, None)
            elif cell.get("vm") and (int(cell.get("vm")) in rich or cell.get("t") == "e"):
                add(row, reference, rich.get(int(cell.get("vm"))))

    sheet_rels = relationships(archive, part)
    part_dir, part_name = posixpath.split(part)
    link_path = posixpath.join(part_dir, "_rels", part_name + ".rels")
    if link_path in archive.namelist():
        external = {node.get("Id"): node.get("Target", "") for node in root(archive, link_path)
                    if node.get("TargetMode", "").lower() == "external"}
        for hyperlink in xml.findall("./{*}hyperlinks/{*}hyperlink"):
            target = external.get(hyperlink.get(REL + "id"), "")
            if re.search(r"\.(?:jpe?g|png|webp|gif|bmp)(?:[?#]|$)", target, re.I):
                reference = hyperlink.get("ref", "")
                match = re.match(r"[A-Z]+([1-9][0-9]*)", reference)
                if match:
                    add(int(match.group(1)), reference, None)
    for link in xml.findall("{*}drawing"):
        drawing = sheet_rels.get(link.get(REL + "id"))
        if not drawing:
            raise PhotoIssue(f"「{sheet}」含未嵌入的图片对象。")
        drawing_rels = relationships(archive, drawing)
        for anchor in root(archive, drawing):
            pictures = list(anchor.iter(XDR + "pic"))
            if not pictures:
                continue
            start = anchor.findtext(f"{XDR}from/{XDR}row")
            if start is None:
                raise PhotoIssue(f"「{sheet}」含无法定位行的浮动图片。请把图片移到物料行后重试。")
            row = int(start) + 1
            for picture in pictures:
                add(row, f"第 {row} 行", media_for(archive, picture, drawing_rels))
    for row, item in by_row.items():
        source = item["source"]
        if IMAGE_LINK.search(source.pop("imageCell", "")) or (
            IMAGE_LINK.search(item["values"]["note"]) and re.search(r"图|photo|image", item["values"]["note"], re.I)
        ):
            if not photos.get(row):
                add(row, f"第 {row} 行", None)
        if source.pop("expectsImage", False) and not photos.get(row):
            add(row, f"第 {row} 行", None)
        if IMAGE_FUNCTION.search(item["values"]["note"]):
            item["values"]["note"] = ""
            source["requestNote"] = ""
    return {row: list(dict.fromkeys(targets)) for row, targets in photos.items()}
