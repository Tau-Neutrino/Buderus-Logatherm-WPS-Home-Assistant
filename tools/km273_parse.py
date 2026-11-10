"""Reassemble and decode a KM273 element table dumped by esphome/buderus-wps-dump.yaml.

Usage:
    python tools/km273_parse.py <dump.log> [--out data/km273_elements_new.csv] [--fhem 26_KM273_.../26_KM273.pm]

Reads the "KM273HEX;<offset>;<hex>" lines from an ESPHome log, checks that the byte ranges are
contiguous and that the CRC32 matches the "KM273DUMP done" line, decodes the entries
(idx u16 | extid 7 bytes | max s32 | min s32 | len s8 | text, 0-terminated; same as FHEM 26_KM273)
and writes a CSV. If the FHEM module is given, its manually assigned 'format' and 'read' columns
are added by element name, and differences in index numbering are reported.
"""
import argparse
import csv
import re
import struct
import sys
import zlib
from pathlib import Path


def load_dump(log_path):
    chunks = {}
    done = None
    size = None
    list_end = None
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        m = re.search(r"KM273HEX;([0-9A-F]{6});([0-9A-F]+)", line)
        if m:
            chunks[int(m.group(1), 16)] = bytes.fromhex(m.group(2))
            continue
        m = re.search(r"KM273DUMP size=(\d+)", line)
        if m:
            size = int(m.group(1))
        m = re.search(r"KM273DUMP done bytes=(\d+) size=\d+ crc32=([0-9A-F]{8})", line)
        if m:
            done = (int(m.group(1)), int(m.group(2), 16))
        m = re.search(r"KM273DUMP list_end raw=([0-9A-F ]+)", line)
        if m:
            list_end = m.group(1).strip()

    if not chunks:
        sys.exit("No KM273HEX lines found in the log.")
    data = bytearray()
    for off in sorted(chunks):
        if off != len(data):
            sys.exit(f"Gap in the dump: expected offset {len(data):#x}, next line starts at {off:#x}. Dump again.")
        data += chunks[off]

    print(f"Reassembled {len(data)} bytes (pump reported size {size}).")
    if done is None:
        print("WARNING: no 'KM273DUMP done' line - the dump did not finish or the log was cut.")
    else:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        if done[0] != len(data) or done[1] != crc:
            sys.exit(f"Mismatch: device sent {done[0]} bytes crc {done[1]:08X}, log has {len(data)} bytes crc {crc:08X}. "
                     "Log lines were lost - dump again.")
        print(f"CRC32 {crc:08X} matches the device.")
    if list_end:
        print(f"End-of-list frame 09FDFFE0: {list_end}")
    return bytes(data)


def parse_entries(data):
    entries = []
    i = 0
    last_idx = -1
    while len(data) - i > 18:
        idx, extid, vmax, vmin, length = struct.unpack(">H7siib", data[i:i + 18])
        if idx <= last_idx or not 1 < length < 100 or i + 18 + length > len(data):
            sys.exit(f"Parse error at byte {i}: idx={idx} len={length}")
        text = data[i + 18:i + 17 + length].decode("ascii", "replace")
        entries.append({"idx": idx, "extid": extid.hex().upper(), "min": vmin, "max": vmax, "text": text})
        last_idx = idx
        i += 18 + length
    if i != len(data):
        print(f"Note: {len(data) - i} trailing bytes after the last entry were ignored.")
    return entries


def load_fhem(pm_path):
    table = {}
    pattern = re.compile(r"'idx'\s*=>\s*(\d+).*?'format'\s*=>\s*'([^']*)'.*?'read'\s*=>\s*(\d+).*?'text'\s*=>\s*'([^']+)'")
    for line in Path(pm_path).read_text(encoding="latin-1").splitlines():
        m = pattern.search(line)
        if m:
            table[m.group(4)] = {"idx": int(m.group(1)), "format": m.group(2), "read": int(m.group(3))}
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out", default="data/km273_elements_new.csv")
    ap.add_argument("--fhem", default=None)
    args = ap.parse_args()

    entries = parse_entries(load_dump(args.log))
    print(f"Decoded {len(entries)} elements (idx {entries[0]['idx']}..{entries[-1]['idx']}).")

    fhem = load_fhem(args.fhem) if args.fhem else {}
    moved = 0
    for e in entries:
        e["read_id"] = f"{0x04003FE0 | (e['idx'] << 14):08X}"
        e["value_id"] = f"{0x0C003FE0 | (e['idx'] << 14):08X}"
        f = fhem.get(e["text"])
        e["fhem_format"] = f["format"] if f else ""
        e["fhem_read"] = f["read"] if f else ""
        e["fhem_idx"] = f["idx"] if f else ""
        if f and f["idx"] != e["idx"]:
            moved += 1
    if fhem:
        known = sum(1 for e in entries if e["fhem_format"])
        names = {e["text"] for e in entries}
        print(f"FHEM table: {known} of {len(entries)} names known, {moved} with a different index, "
              f"{sum(1 for t in fhem if t not in names)} FHEM names not in this pump.")

    cols = ["idx", "text", "min", "max", "extid", "read_id", "value_id", "fhem_format", "fhem_read", "fhem_idx"]
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter=";")
        w.writeheader()
        w.writerows(entries)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
