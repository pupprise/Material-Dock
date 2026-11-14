import argparse
import ipaddress
import json
import secrets
from getpass import getpass
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from accounts import key_digest, validate_roster
from credentials import password_record


def write_invite(path, name, key, password, origin):
    link = f"{origin}/#{urlencode({'name': name, 'key': key})}"
    info = (
        f"Material Dock · 个人登录信息 / Personal sign-in details\r\n\r\n"
        f"姓名 / Username: {name}\r\n"
        f"初始密码 / Initial password: {password}\r\n"
        f"专属密钥 / Personal key: {key}\r\n"
        f"个人链接 / Personal link: {link}\r\n\r\n"
        "打开个人链接，输入当前密码。仅在未改密码或恢复初始密码后使用初始密码。\r\n"
        "忘记密码时，从个人链接打开登录页，再点击「忘记密码」。请妥善保管并仅本人使用。\r\n"
        "Open the personal link and use your current password. Use the initial password for a new or reset account.\r\n"
        "To recover your account, open the same link and select Forgot Password. Keep this package private.\r\n"
    )
    with ZipFile(path, "x", ZIP_DEFLATED) as archive:
        archive.writestr("Material Dock.url", f"[InternetShortcut]\r\nURL={link}\r\n")
        archive.writestr("个人登录信息.txt", info.encode("utf-8-sig"))


def initialize(args):
    root = Path(__file__).resolve().parent
    config_path = root / "config.json"
    private = root / "private"
    if config_path.exists() or private.exists() or (root / "data" / "dock.sqlite3").exists():
        raise ValueError("A config, invite folder or database already exists. Initialize in a fresh checkout.")

    names = [*args.designer, args.materials]
    if len(set(names)) != len(names):
        raise ValueError("Account names must be unique.")
    members = {name: "owner" for name in args.designer}
    members[args.materials] = "materials"
    keys = {name: secrets.token_urlsafe(32) for name in members}
    hashes = {name: key_digest(key) for name, key in keys.items()}
    validate_roster(members, hashes)
    interface = ipaddress.IPv4Interface(args.interface)
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    for origin in args.https_origin:
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Use an HTTPS origin without a path, query, or fragment.")
    password = getpass("Initial password: ") if args.prompt_password else secrets.token_urlsafe(18)
    if not 8 <= len(password) <= 64 or password != password.strip():
        raise ValueError("Use 8–64 characters without surrounding whitespace.")

    config = {
        "interface": str(interface),
        "port": args.port,
        "storagePath": "data",
        "peerNetworks": [str(ipaddress.IPv4Network(network)) for network in args.peer_network],
        "httpsProxyOrigins": args.https_origin,
        "members": members,
        "initialPasswordHash": password_record(password),
        "recoveryHashes": hashes,
    }
    private.mkdir(mode=0o700)
    for index, name in enumerate(members, start=1):
        write_invite(private / f"member-{index:02d}.zip", name, keys[name], password, f"http://{interface.ip}:{args.port}")
    (private / "roster.txt").write_text(
        "".join(f"member-{index:02d}.zip\t{name}\n" for index, name in enumerate(members, start=1)),
        encoding="utf-8-sig",
    )
    with config_path.open("x", encoding="utf-8") as target:
        json.dump(config, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print("Initialized config.json. Personal invites and roster are in private/.")


def main():
    parser = argparse.ArgumentParser(description="Material Dock administration")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="initialize a fresh deployment")
    init.add_argument("--designer", action="append", required=True)
    init.add_argument("--materials", required=True)
    init.add_argument("--interface", default="127.0.0.1/32")
    init.add_argument("--port", type=int, default=8765)
    init.add_argument("--peer-network", action="append", default=[])
    init.add_argument("--https-origin", action="append", default=[])
    init.add_argument("--prompt-password", action="store_true")
    try:
        initialize(parser.parse_args())
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
