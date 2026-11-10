"""List or remove Home Assistant entity registry entries (WebSocket API).

Token: read from %USERPROFILE%\\.ha_token (never printed).

Usage:
    python tools/ha_entities.py list <substring> [--unavailable]
    python tools/ha_entities.py remove <entity_id> [<entity_id> ...]
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import websockets

URL = os.environ.get("HA_WS_URL", "ws://homeassistant.local:8123/api/websocket")


async def session():
    token = Path(os.path.expanduser("~/.ha_token")).read_text(encoding="utf-8").strip()
    ws = await websockets.connect(URL, max_size=None)
    await ws.recv()  # auth_required
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    if json.loads(await ws.recv()).get("type") != "auth_ok":
        sys.exit("Authentication failed")
    return ws


async def call(ws, msg_id, payload):
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == msg_id:
            return r


async def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    ws = await session()
    try:
        if cmd == "list":
            needle = args[0].lower()
            only_unavail = "--unavailable" in args
            reg = (await call(ws, 1, {"type": "config/entity_registry/list"}))["result"]
            states = {s["entity_id"]: s for s in (await call(ws, 2, {"type": "get_states"}))["result"]}
            rows = []
            for e in reg:
                eid = e["entity_id"]
                if needle not in eid.lower():
                    continue
                st = states.get(eid)
                state = st["state"] if st else "(no state)"
                orphan = st is None or state == "unavailable"
                if only_unavail and not orphan:
                    continue
                rows.append((eid, state, e.get("platform"), e.get("original_name") or ""))
            for r in sorted(rows):
                print(f"{r[0]:70} {r[1]:12} {r[2]:10} {r[3]}")
            print(f"{len(rows)} entities")
        elif cmd == "remove":
            for i, eid in enumerate(args, start=10):
                r = await call(ws, i, {"type": "config/entity_registry/remove", "entity_id": eid})
                print(f"{eid}: {'removed' if r.get('success') else r.get('error')}")
        else:
            sys.exit(__doc__)
    finally:
        await ws.close()


asyncio.run(main())
