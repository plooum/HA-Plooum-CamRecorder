"""End-to-end checks of Plooum CamRecorder against the dev Home Assistant (dev/ha.sh start).
Run with the Home Assistant venv: <venv>/bin/python dev/scenario.py. Exits 1 on a failure."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import aiohttp

URL = os.environ.get("HA_URL", "http://127.0.0.1:8130")
TOKEN = (Path(__file__).parent / ".ha-token").read_text().strip()
H = {"Authorization": f"Bearer {TOKEN}"}
D = "plooum_camrecorder"
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return -1


async def main() -> None:
    async with aiohttp.ClientSession(headers=H) as s:
        async def call(service, data, response=True):
            url = f"{URL}/api/services/{D}/{service}" + ("?return_response" if response else "")
            async with s.post(url, json=data) as r:
                body = await r.text()
                return r.status, (json.loads(body) if body.startswith(("{", "[")) else body)

        async def state(eid):
            async with s.get(f"{URL}/api/states/{eid}") as r:
                return await r.json()

        # Saved events, through the websocket.
        events: list[dict] = []
        ws = await s.ws_connect(f"{URL}/api/websocket")
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": TOKEN})
        await ws.receive_json()
        await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": f"{D}_saved"})
        await ws.receive_json()

        async def listen():
            async for msg in ws:
                data = msg.json()
                if data.get("type") == "event":
                    events.append(data["event"]["data"])
        listener = asyncio.create_task(listen())

        # Photos of both cameras in one call.
        st, res = await call("snapshot", {"entity_id": ["camera.live", "camera.still"]})
        files = res.get("service_response", {}).get("files", []) if isinstance(res, dict) else []
        check("snapshot: two photos", st == 200 and len(files) == 2 and all(os.path.getsize(f["path"]) > 1000 for f in files), res)
        check("snapshot: media_content_id", all(f["media_content_id"].startswith("media-source://media_source/local/plooum_camrecorder/") for f in files), files)
        st, res = await call("snapshot", {"entity_id": "camera.still"})
        p2 = res["service_response"]["files"][0]["path"]
        check("snapshot: second photo in the same second gets a free name", p2 != files[1]["path"], p2)
        last = await state("sensor.still_last_photo")
        check("sensor last photo", last["attributes"]["path"] == p2, last)

        # Fixed duration.
        st, res = await call("record", {"entity_id": "camera.live", "duration": 5})
        v = res["service_response"]["files"][0] if st == 200 else {}
        d = duration(v.get("path", ""))
        name = os.path.basename(v.get("path", "x-x.mp4"))[:-4].split("-")
        check("video name spans its real length", abs(int(name[1]) - int(name[0]) - d) <= 1.5, (name, d))
        check("record 5 s", st == 200 and 4 <= d <= 11 and v["path"].endswith(".mp4") and "-recording" not in v["path"], (st, res, d))

        # Video on a camera without stream: clear error.
        st, res = await call("record", {"entity_id": "camera.still", "duration": 2})
        check("record without stream fails", st == 500 or st == 400, (st, res))

        # Start, stop early.
        st, res = await call("start_recording", {"entity_id": "camera.live", "max_duration": 120}, response=False)
        check("start_recording", st == 200, res)
        await asyncio.sleep(1)
        sw = await state("switch.live_recording")
        check("switch on while recording", sw["state"] == "on" and sw["attributes"]["started"], sw)
        st, res = await call("start_recording", {"entity_id": "camera.live"}, response=False)
        check("second start fails", st in (400, 500), (st, res))
        await asyncio.sleep(7)
        st, res = await call("stop_recording", {"entity_id": "camera.live"})
        v = res["service_response"]["files"][0] if st == 200 else {}
        d = duration(v.get("path", ""))
        check("stop_recording after ~8 s", st == 200 and 5 <= d <= 12, (st, res, d))
        sw = await state("switch.live_recording")
        check("switch off after stop", sw["state"] == "off", sw)
        st, res = await call("stop_recording", {"entity_id": "camera.live"})
        check("stop when not recording fails", st == 400, (st, res))

        # Through the switch.
        async with s.post(f"{URL}/api/services/switch/turn_on", json={"entity_id": "switch.live_recording"}) as r:
            pass
        await asyncio.sleep(5)
        async with s.post(f"{URL}/api/services/switch/turn_off", json={"entity_id": "switch.live_recording"}) as r:
            ok = r.status == 200
        last = await state("sensor.live_last_video")
        d = duration(last["attributes"]["path"] or "")
        check("switch on / off makes a video", ok and 3 <= d <= 9, (last, d))

        # Lookback: the camera's HLS stream must run (someone watching).
        await ws.send_json({"id": 2, "type": "camera/stream", "entity_id": "camera.live"})
        await asyncio.sleep(8)
        st, res = await call("record", {"entity_id": "camera.live", "duration": 3, "lookback": 4})
        v = res["service_response"]["files"][0] if st == 200 else {}
        d = duration(v.get("path", ""))
        check("record 3 s with 4 s lookback", st == 200 and d >= 5.5, (st, res, d))

        await asyncio.sleep(0.5)
        kinds = [e["kind"] for e in events]
        check("saved events", kinds.count("photo") == 3 and kinds.count("video") == 4, events)
        listener.cancel()
        await ws.close()
    print(f"\n{len(failures)} failure(s)" if failures else "\nAll passed")
    sys.exit(1 if failures else 0)


asyncio.run(main())
