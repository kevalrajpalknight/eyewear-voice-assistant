import asyncio
from datetime import datetime

from bleak import BleakClient, BleakScanner
from rich.console import Console

from common import ascii_of, get_logger, load_target

LISTEN_SECONDS = 60

console = Console()
log = get_logger("listen")


async def main() -> None:
    target = load_target()
    device = await BleakScanner.find_device_by_address(target["address"], timeout=15)
    if device is None:
        raise SystemExit("Not found. Toggle glasses Bluetooth, make sure the phone is disconnected, retry.")

    async with BleakClient(device) as client:
        subscribed = 0
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" not in ch.properties and "indicate" not in ch.properties:
                    continue

                def handler(_, data: bytearray, ch=ch) -> None:
                    raw = bytes(data)
                    line = f"{ch.uuid} h={ch.handle} {raw.hex()} '{ascii_of(raw)}'"
                    console.print(datetime.now().strftime("%H:%M:%S.%f")[:-3], line)
                    log.info(line)

                try:
                    await client.start_notify(ch, handler)
                    subscribed += 1
                    console.print(f"subscribed {ch.uuid} h={ch.handle}")
                except Exception as e:
                    console.print(f"[red]subscribe failed {ch.uuid}: {e!r}")
        console.print(f"{subscribed} subscriptions. Press buttons / tap / wake word now ({LISTEN_SECONDS}s)...")
        await asyncio.sleep(LISTEN_SECONDS)


asyncio.run(main())
