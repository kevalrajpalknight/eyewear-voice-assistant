import asyncio
import json

from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.tree import Tree

from common import LOG_DIR, ascii_of, get_logger, load_target

STANDARD = {"0000180a": "Device Information", "0000180f": "Battery", "00001800": "Generic Access",
            "00001801": "Generic Attribute", "0000ae00": "Jieli custom (hypothesis)"}
DEVINFO = {"2a29": "manufacturer", "2a24": "model", "2a26": "firmware", "2a27": "hardware",
           "2a28": "software", "2a25": "serial"}

console = Console()
log = get_logger("enumerate")


async def main() -> None:
    target = load_target()
    console.print(f"Looking for {target['address']}...")
    device = await BleakScanner.find_device_by_address(target["address"], timeout=15)
    if device is None:
        raise SystemExit("Not found. Toggle glasses Bluetooth, make sure the phone is disconnected, retry.")

    dump, summary = [], {}
    async with BleakClient(device) as client:
        mtu = client.mtu_size
        console.print(f"Connected. MTU: {mtu}")
        tree = Tree(f"{target['name']} {target['address']} (MTU {mtu})")
        for svc in client.services:
            label = STANDARD.get(svc.uuid[:8], "")
            node = tree.add(f"[bold]{svc.uuid}[/] {label}")
            svc_d = {"uuid": svc.uuid, "label": label, "characteristics": []}
            for ch in svc.characteristics:
                ch_d = {"uuid": ch.uuid, "handle": ch.handle, "properties": ch.properties, "descriptors": []}
                cnode = node.add(f"{ch.uuid} h={ch.handle} \\[{','.join(ch.properties)}]")
                if "read" in ch.properties:
                    try:
                        val = bytes(await client.read_gatt_char(ch))
                        ch_d["value_hex"], ch_d["value_ascii"] = val.hex(), ascii_of(val)
                        cnode.add(f"value: {val.hex()}  '{ascii_of(val)}'")
                        if svc.uuid.startswith("0000180a") and ch.uuid[4:8] in DEVINFO:
                            summary[DEVINFO[ch.uuid[4:8]]] = val.decode(errors="replace")
                        if ch.uuid.startswith("00002a19"):
                            summary["battery_%"] = val[0]
                    except Exception as e:
                        ch_d["read_error"] = repr(e)
                        cnode.add(f"[red]read failed: {e!r}")
                for d in ch.descriptors:
                    ch_d["descriptors"].append({"uuid": d.uuid, "handle": d.handle})
                    cnode.add(f"descriptor {d.uuid} h={d.handle}")
                svc_d["characteristics"].append(ch_d)
            dump.append(svc_d)

    console.print(tree)
    console.print("Summary:", summary or "no Device Info / Battery readable")
    out = LOG_DIR / "gatt_dump.json"
    out.write_text(json.dumps({"target": target, "mtu": mtu, "summary": summary, "services": dump}, indent=2),
                   encoding="utf-8")
    log.info("dump saved %s", out)
    console.print(f"Saved {out}")


asyncio.run(main())
