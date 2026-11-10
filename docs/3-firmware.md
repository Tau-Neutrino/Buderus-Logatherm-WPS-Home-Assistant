# 3 · Firmware (ESPHome)

There are three firmwares. They all use the same hardware and pins, and you switch between them by flashing (over the air once the first one runs):

| File | Mode on the bus | Purpose |
|---|---|---|
| [`buderus-wps.yaml`](../esphome/buderus-wps.yaml) | **LISTENONLY**: never transmits, not even ACKs | First test: does the adapter receive the pump's frames? |
| [`buderus-wps-dump.yaml`](../esphome/buderus-wps-dump.yaml) | NORMAL, reads only | Service: downloads the pump's **element table** (the list of all parameters with their indices) |
| [`buderus-wps-monitor.yaml`](../esphome/buderus-wps-monitor.yaml) | NORMAL, reads only | **Normal operation**: reads the parameters from `tools/monitor_params.csv` and publishes them to Home Assistant. This file is **generated**, so don't edit it by hand. |

All three have the same ESPHome device name (`buderus-wps`), so Home Assistant keeps one device.

## Prerequisites

- Python 3.11+ and ESPHome: `pip install esphome` (tested with ESPHome 2026.6).
  - **Windows:** run `esphome` from PowerShell or cmd, **not** from Git Bash/MSYS. The ESP-IDF toolchain refuses to build in an MSYS shell.
- The Python helpers in `tools/` need: `pip install -r tools/requirements.txt`
- Secrets: copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` and fill it in. `secrets.yaml` is git-ignored.

```bash
cd esphome
cp secrets.example.yaml secrets.yaml
# generate an API key:
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

## Step 1: listen-only test

Flash over USB at your desk (buck converter **not** connected), then install the adapter as described in [step 2](2-pump-connection.md):

```bash
cd esphome
esphome run buderus-wps.yaml          # first time: choose the USB port
esphome logs buderus-wps.yaml         # later: watch the log over Wi-Fi
```

What you should see:
- The **CAN frame rate** sensor shows ≈ 2–3 frames/s.
- With the switch **CAN sniffer log** on, every new or changed frame is logged:

```
[I][km273]: RX T 08000270 [2] 01 A4
[I][km273]: RX T 08004270 [2] 03 1F
```

These `0x08xxx270` frames are the boards broadcasting raw sensor values (see [protocol](5-protocol.md#broadcast-frames)). If you see them, the receive side works.

## Step 2: dump the element table of your pump

The pump's controller holds a table of all its parameters (≈ 1800 entries: name, index, min, max). **The indices differ between controller software versions.** On the reference pump, 1573 of 1792 indices differ from the ones hard-coded in the FHEM module, mostly shifted by one. So read **your own** table once before reading values:

```bash
cd esphome
esphome run buderus-wps-dump.yaml
cd ..
python tools/esphome_press.py esphome/secrets.yaml buderus-wps.local "Dump element table" dump.log "KM273DUMP (done|failed)" 600
python tools/km273_parse.py dump.log --out data/km273_elements_mypump.csv
```

`esphome_press.py` presses the **Dump element table** button over the ESPHome API and records the log until the dump finishes (≈ 30 s for 76 kB). You can also press the button in Home Assistant and save the output of `esphome logs` to a file.

Expected output of the parser:

```
Reassembled 76279 bytes (pump reported size 76279).
CRC32 6EB27612 matches the device.
End-of-list frame 09FDFFE0: 6E B2 76 12
Decoded 1792 elements (idx 0..2605).
Wrote data/km273_elements_mypump.csv
```

The CRC check covers the whole table, so if it matches, the dump is complete and correct. If it fails, simply run the dump again.

Optional: `--fhem path/to/26_KM273.pm` adds the FHEM module's format columns and reports how many indices differ from FHEM.

## Step 3: choose parameters and generate the monitor firmware

[`tools/monitor_params.csv`](../tools/monitor_params.csv) lists what the monitor reads (semicolon-separated):

```csv
group;param;label;unit;scale;decimals;interval_s
State;HW_COMPRESSOR;Compressor;;1;0;10
Temperatures;GT3_TEMP;Hot water GT3;°C;0.1;1;30
Statistics;STATS_ENERGY_OUTPUT_HEATING;Heat produced heating;kWh;0.01;0;300
```

| Column | Meaning |
|---|---|
| `group` | Only for grouping in the file |
| `param` | Parameter **name** from the element table (`km273_dictionary.csv`) |
| `label` | Name of the sensor in ESPHome/Home Assistant. **Keep it stable**: renaming creates a new entity and the history is lost |
| `unit` | `°C` → temperature sensor, `kWh` → energy counter (`total_increasing`, usable in the energy dashboard), `h` and empty `""` → plain values |
| `scale` | Raw value × scale. Temperatures are 0.1 °C, energy 0.01 kWh, runtimes in seconds (`0.000277777778` → hours) |
| `decimals` | Displayed decimals |
| `interval_s` | How often this parameter is read. 10 s for states, 30 s for temperatures, 300–900 s for counters and settings |

Generate the firmware and flash it:

```bash
python tools/km273_gen_monitor.py --elements data/km273_elements_mypump.csv
cd esphome
esphome run buderus-wps-monitor.yaml
```

Without `--elements`, the indices of the reference pump (`km273_dictionary.csv`, WPS 8K-1) are used. Only do that if your dump is identical (same CRC `6EB27612`).

### Example: add a parameter

You want the speed of the heat carrier pump G2 and the heat pump return temperature GT9. Search the dictionary:

```bash
grep -i -E "PUMP_G2|GT9_TEMP" km273_dictionary.csv
```

```
752;GT8_TEMP;Heat pump flow GT8;...
760;GT9_TEMP;Heat pump return GT9;°C;0.1;...
1148;HW_PUMP_G2_ANALOG;Heat carrier pump G2 speed;%;...;Heat carrier pump speed: ~37 % idle, ~71 % running
```

Add two lines to `tools/monitor_params.csv`, regenerate, flash:

```csv
Pumps;HW_PUMP_G2_ANALOG;Heat carrier pump G2 speed;%;1;0;30
Temperatures;GT9_TEMP;Heat pump return GT9;°C;0.1;1;30
```

Two new sensors appear in Home Assistant: `sensor.buderus_wps_heat_carrier_pump_g2_speed` and `sensor.buderus_wps_heat_pump_return_gt9`. If a name doesn't exist in your table, the generator stops with `Not in this pump's table: [...]`.

Tips for finding parameters:
- `km273_dictionary.csv` has a `label` and `notes` column for the parameters already identified on the reference pump.
- Read a candidate once with a short interval, compare it with the pump's display, then set the final interval.
- 2-byte values are signed. `0xDEAD` means "no sensor" and is published as *unknown*.

## What the monitor firmware contains

Besides one sensor per CSV line, the generator adds:

| Entity | Meaning |
|---|---|
| **Hot water (display)** | GT3 + display offset (`DHW_CALCULATED_SETPOINT_TEMP_OFFSET`, factory 4 K). This is the value the pump's display shows as hot water temperature. |
| **Heating curve left/right point (display)** | Heating curve end points including the parallel offset, as on the display |
| **Estimated COP**, **… heating**, **… hot water** | COP estimated from the datasheet curves (brine in GT10, heat pump flow GT8). It is *unknown* when the compressor is off and for 60 s after a start or a heating↔hot water switch. |
| **Operating mode** | `Standby`, `Heizen` or `Warmwasser`, from the compressor and the diverter valve |
| **Flow with setpoint** | Text like `25,4 °C (Soll: 28,2 °C)` for a dashboard tile |
| **Compressor running** | Binary sensor (device class *running*) |
| **CAN frame rate** | Diagnostics: received frames per second |
| **CAN sniffer log** | Switch: logs every new/changed frame (off by default) |

The COP is an **estimate** from the datasheet (`km273_cop.h`: COP over brine temperature for flow 35/45/55 °C), because the WPS has no electrical power measurement. For a real COP you need an electricity meter in the pump's supply.

### How the poller works

`km273_poller.h` sends **one request at a time**. It always picks the parameter that is most overdue relative to its own interval, waits up to 300 ms for the answer, and keeps 50 ms between requests. That is at most ≈ 20 requests/s, far below what the bus can carry, and in practice much less, because most parameters are read every 30–900 s. A parameter that doesn't answer three times in a row is published as *unknown*.

## Updating

- Change `tools/monitor_params.csv`, run the generator, `esphome run buderus-wps-monitor.yaml`. OTA takes about 30 s, and the pump isn't affected.
- After a **software update of the pump**, dump the element table again and regenerate with `--elements`. The indices may have changed.

Next: [4 · Home Assistant](4-home-assistant.md)
