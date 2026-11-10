"""Press an ESPHome button over the native API, record the device log, stop on a pattern.

Usage:
    python tools/esphome_press.py <secrets.yaml> <host> <button name> <logfile> <stop regex> [timeout_s]

Example (element table dump):
    python tools/esphome_press.py esphome/secrets.yaml buderus-wps.local "Dump element table" dump.log "KM273DUMP (done|failed)" 600
"""
import asyncio
import re
import sys

from aioesphomeapi import APIClient, LogLevel


async def main(secrets_path, host, button, logfile, stop, timeout):
    secrets = open(secrets_path, encoding="utf-8").read()
    key = re.search(r"^api_encryption_key:\s*\"?([^\"\s]+)", secrets, re.M).group(1)
    stop_re = re.compile(stop)
    finished = asyncio.Event()
    count = 0

    with open(logfile, "w", encoding="utf-8") as out:
        def on_log(msg):
            nonlocal count
            text = msg.message.decode("utf-8", "replace") if isinstance(msg.message, bytes) else msg.message
            text = re.sub(r"\x1b\[[0-9;]*m", "", text)
            out.write(text + "\n")
            count += 1
            if "KM273HEX" not in text:
                print(text[:200], flush=True)
            if stop_re.search(text):
                finished.set()

        cli = APIClient(host, 6053, None, noise_psk=key)
        await cli.connect(login=True)
        cli.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_DEBUG)
        entities, _ = await cli.list_entities_services()
        buttons = {e.name: e.key for e in entities if type(e).__name__ == "ButtonInfo"}
        if button not in buttons:
            sys.exit(f"Button '{button}' not found; available: {list(buttons)}")
        await asyncio.sleep(3)
        cli.button_command(buttons[button])
        try:
            await asyncio.wait_for(finished.wait(), timeout)
        except asyncio.TimeoutError:
            print(f"Timeout after {timeout} s without matching '{stop}'.")
        await asyncio.sleep(2)
        await cli.disconnect()
    print(f"{count} log lines written to {logfile}")


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1], a[2], a[3], a[4], float(a[5]) if len(a) > 5 else 300))
