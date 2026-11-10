"""Replace one view of a Home Assistant (storage-mode) dashboard with a local view YAML.

Token: read from %USERPROFILE%\\.ha_token (never printed).
Before saving, the complete current dashboard config is written to homeassistant/backups/.

Usage:
    python tools/ha_dashboard.py push <dashboard> <view.yaml>
        <dashboard>: "lovelace" for the default dashboard, otherwise its url_path
    python tools/ha_dashboard.py views <dashboard>
"""
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

import websockets
import yaml

URL = os.environ.get("HA_WS_URL", "ws://homeassistant.local:8123/api/websocket")
ROOT = Path(__file__).resolve().parent.parent


async def call(ws, msg_id, payload):
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == msg_id:
            if not r.get("success"):
                sys.exit(f"{payload['type']} failed: {r.get('error')}")
            return r.get("result")


async def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, dash = sys.argv[1], sys.argv[2]
    url_path = None if dash == "lovelace" else dash
    token = Path(os.path.expanduser("~/.ha_token")).read_text(encoding="utf-8").strip()
    ws = await websockets.connect(URL, max_size=None)
    await ws.recv()
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    if json.loads(await ws.recv()).get("type") != "auth_ok":
        sys.exit("Authentication failed")
    try:
        config = await call(ws, 1, {"type": "lovelace/config", "url_path": url_path})
        views = config.get("views", [])
        if cmd == "views":
            for v in views:
                print(f"{v.get('path', '-'):20} {v.get('title', '')}")
            return
        if cmd != "push" or len(sys.argv) < 4:
            sys.exit(__doc__)
        view = yaml.safe_load(Path(sys.argv[3]).read_text(encoding="utf-8"))
        path = view.get("path")
        idx = next((i for i, v in enumerate(views) if v.get("path") == path), None)
        if idx is None:
            sys.exit(f"No view with path '{path}' in dashboard '{dash}'. Existing: {[v.get('path') for v in views]}")

        backup_dir = ROOT / "homeassistant" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"{dash}-{stamp}.json"
        backup.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

        views[idx] = view
        config["views"] = views
        await call(ws, 2, {"type": "lovelace/config/save", "url_path": url_path, "config": config})
        print(f"View '{path}' replaced (position {idx + 1} of {len(views)}). Backup: {backup.relative_to(ROOT)}")
    finally:
        await ws.close()


asyncio.run(main())
