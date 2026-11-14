import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

from accounts import key_digest
from credentials import password_record
from server import DockServer, RELEASE, create_batch, event
from reference_photos import save_photo
from suppliers import save as save_supplier


ROOT = Path(__file__).resolve().parent
STORE = ROOT / "demo-data"
CONFIG = STORE / "settings.json"
DESIGNERS = ("样例1", "样例2")
COORDINATOR = "样例3"


def sample_photo():
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    width, height = 168, 96
    scan = bytearray()
    for y in range(height):
        scan.append(0)
        for x in range(width):
            strap = 25 < y < 72
            teeth = 44 < y < 53 and x % 7 < 4
            slider = 90 < x < 122 and 35 < y < 62
            if slider:
                color = (173, 176, 179)
            elif teeth:
                color = (185, 185, 181)
            elif strap:
                shade = 47 + ((x + y) % 4) * 5
                color = (shade, shade + 8, shade + 15)
            else:
                shade = 220 + ((x + y) % 4) * 2
                color = (shade, shade - 3, shade - 8)
            scan.extend(color)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(
        b"IDAT", zlib.compress(scan)
    ) + chunk(b"IEND", b"")


def seed(server):
    from io import BytesIO

    with closing(server.database()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM settings WHERE key = 'demo_seeded'").fetchone():
            return
        save_supplier(db, {"name": "样例1供应商", "alias": "样链 SZ", "categories": "尼龙拉链、反穿拉链"})
        save_supplier(db, {"name": "样例2供应商", "alias": "样带", "categories": "弹力包条、松紧带"})
        save_supplier(db, {"name": "样例3供应商", "alias": "样辅", "categories": "反光贴、卡扣、标签"})
        fingerprint, mime = save_photo(server.photos, BytesIO(sample_photo()))
        photo = "4" * 32
        db.execute("INSERT INTO photo_uploads VALUES (?, ?, ?, ?)", (photo, DESIGNERS[0], fingerprint, mime))
        today = date.today()
        batches = (
            (DESIGNERS[0], "样例1项目", "试样1",
             [("前门拉链", 6, "样例1供应商", "active", True), ("袋口拉链", 7, "样例1供应商", "active", False),
              ("弹力包条", 10, "样例2供应商", "active", False)]),
            (DESIGNERS[0], "样例1项目", "试样1",
             [("反光贴", -2, "样例3供应商", "active", False), ("样衣标签", 8, "样例3供应商", "held", False)]),
            (DESIGNERS[1], "样例2项目", "试样2",
             [("尼龙搭扣", 8, "样例3供应商", "done", False), ("调节织带", 12, "样例2供应商", "active", False)]),
        )
        for index, (designer, project, style, lines) in enumerate(batches):
            rows = [{
                "project": project, "style": style, "purpose": "打样", "name": name,
                "specification": {"前门拉链": "拉链长度 70 cm · 5#反穿", "弹力包条": "宽度 35 mm · 厚 1.2 mm",
                                  "尼龙搭扣": "卡扣内径 25 mm"}.get(name, "演示规格"),
                "color": "深灰", "quantity": "10", "unit": "件", "requiredBy": (today + timedelta(days=days)).isoformat(),
                "urgent": urgent, "photoIds": [photo] if name == "前门拉链" else [],
            } for name, days, _, _, urgent in lines]
            result = create_batch(db, {"rows": rows}, {"name": designer, "role": "owner"})
            for name, days, supplier, state, _ in lines:
                item_id = db.execute(
                    "SELECT id FROM items WHERE batch_id = (SELECT batch_id FROM items WHERE id = ?) AND name = ?",
                    (result["id"], name),
                ).fetchone()[0]
                created_at = (today - timedelta(days=index)).isoformat() + "T09:00:00+08:00"
                db.execute(
                    "UPDATE items SET supplier = ?, lifecycle = ?, received = ?, created_at = ?, eta = ? WHERE id = ?",
                    (supplier, "held" if state == "held" else "active", 100000 if state == "done" else 0,
                     created_at, today.isoformat() if name == "弹力包条" else None, item_id),
                )
                if state == "held":
                    event(db, item_id, "已搁置", f"{designer} · 等样衣确认标签内容。", "materials")
        db.execute("INSERT INTO settings VALUES ('demo_seeded', '1')")


def setup():
    STORE.mkdir(exist_ok=True)
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    password = secrets.token_urlsafe(14)
    keys = {name: secrets.token_urlsafe(24) for name in (*DESIGNERS, COORDINATOR)}
    config = {
        "interface": "127.0.0.1/32", "port": port,
        "members": {**dict.fromkeys(DESIGNERS, "owner"), COORDINATOR: "materials"},
        "initialPasswordHash": password_record(password),
        "recoveryHashes": {name: key_digest(key) for name, key in keys.items()},
        "demoKeys": keys,
    }
    (STORE / "样例1-登录密码.txt").write_text(password + "\n", encoding="utf-8")
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    temporary.replace(CONFIG)
    return config


@contextmanager
def startup_lock():
    STORE.mkdir(exist_ok=True)
    with (STORE / "startup.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def ensure_service(origin):
    opener = build_opener(ProxyHandler({}))

    def ready():
        try:
            with opener.open(origin + "/versionz", timeout=0.5) as response:
                return json.load(response).get("version") == RELEASE
        except (OSError, ValueError):
            return False

    if not ready():
        with (STORE / "server.log").open("ab") as log:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, **options)
        (STORE / "server.pid").write_text(str(child.pid), encoding="ascii")
        for _ in range(35):
            if ready():
                break
            if child.poll() is not None:
                raise RuntimeError("演示启动失败，请查看 demo-data/server.log。")
            time.sleep(0.2)
        else:
            raise RuntimeError("演示启动超时，请查看 demo-data/server.log。")


def run(role):
    if role == "serve":
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        with DockServer(config, STORE / "demo.sqlite3") as server:
            seed(server)
            server.serve_forever()
        return
    name = {"designer": DESIGNERS[0], "other": DESIGNERS[1], "materials": COORDINATOR}[role]
    with startup_lock():
        config = setup()
        origin = f"http://127.0.0.1:{config['port']}"
        ensure_service(origin)
    if "--no-browser" not in sys.argv:
        webbrowser.open(origin + "/#" + urlencode({"name": name, "key": config["demoKeys"][name]}))
    print(origin)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "designer")
