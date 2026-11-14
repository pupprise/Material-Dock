import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parent
EXPECTED = b"Material Dock Pilot OK\n"
VERSION = "0.5.1"


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ip = config["interface"].split("/")[0]
    origin = f"http://{ip}:{config['port']}"
    store = ROOT / config["storagePath"]
    store.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))

    def running_version():
        try:
            with opener.open(origin + "/healthz", timeout=1) as response:
                if response.read(128) != EXPECTED:
                    return None
        except (URLError, OSError):
            return None
        try:
            with opener.open(origin + "/versionz", timeout=1) as response:
                return json.load(response).get("version", "0.1")
        except (URLError, OSError, ValueError):
            return "0.1"

    current = running_version()
    if current and current != VERSION:
        raise RuntimeError(f"Material Dock {current} is already running. Stop it before starting {VERSION}.")
    if current is None:
        log_path = store / "server.log"
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                   if os.name == "nt" else {"start_new_session": True})
        with log_path.open("ab") as log:
            server = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "server.py")],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **options,
            )
        (store / "server.pid").write_text(str(server.pid), encoding="ascii")
        for attempt in range(20):
            if running_version() == VERSION:
                break
            if server.poll() is not None:
                raise RuntimeError(f"Dock could not start. See {log_path}")
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Dock health check timed out. See {log_path}")
    if "--no-browser" not in sys.argv:
        webbrowser.open(origin + "/")
    print(f"Material Dock ready: {origin}/")


if __name__ == "__main__":
    main()
