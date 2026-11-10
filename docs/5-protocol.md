# 5 · Protocol reference (KM273)

Everything here was decoded by **mike3436** for the FHEM module 26_KM273 ([FHEM forum, topic 47508](http://forum.fhem.de/index.php/topic,47508.0.html)). It was re-checked on a WPS 8K-1 with this project. Buderus calls the controller protocol "KM273" after the KM273 gateway module.

## Physical layer

- CAN 2.0B, **125 kbit/s**, **29-bit extended IDs**
- Connector on the PEL board: +12 V, GND, CAN-H, CAN-L (see [step 2](2-pump-connection.md))

## Reading a parameter

Every parameter has an **index** `idx` (0 … ≈ 2600) in the controller's element table.

| Direction | CAN ID | Frame |
|---|---|---|
| Request (adapter → pump) | `0x04003FE0 \| (idx << 14)` | **remote frame** (RTR), no data |
| Answer (pump → adapter) | `0x0C003FE0 \| (idx << 14)` | data frame, value **big-endian** |

Example, `GT3_TEMP` (hot water tank) with idx 682 on the reference pump:

```
idx << 14         = 682 × 16384 = 0x00AA8000
request  ID       = 0x04003FE0 | 0x00AA8000 = 0x04AABFE0   (RTR, DLC 0)
answer   ID       = 0x0C003FE0 | 0x00AA8000 = 0x0CAABFE0
answer   data     = 01 C6  → 0x01C6 = 454 → × 0.1 = 45.4 °C
```

Value rules:
- 1, 2 or 4 bytes. 2- and 4-byte values are **signed**.
- `0xDEAD` (2 bytes) = sensor not present.
- Scaling depends on the parameter: temperatures ×0.1 °C, energy ×0.01 kWh, runtimes in seconds, states 0/1. `km273_dictionary.csv` holds the scale and format used here (format names as in FHEM: `tem` = temperature, `int` = integer, …).
- `read_id` and `value_id` are precomputed in `km273_dictionary.csv` for every parameter.

Writing works the same way: FHEM sends the new value as a 2-byte data frame. **This project does not write.** A wrong index silently changes a different setting.

## The element table

The controller describes all its parameters in a table (≈ 76 kB on the WPS 8K-1, 1792 elements). It is the same transfer a Buderus KM200 gateway performs at startup:

| Step | CAN ID | Frame | Content |
|---|---|---|---|
| 1. Ask for the size | `0x01FD7FE0` | RTR | |
| Answer | `0x09FD7FE0` | data | `00 01 29 F7 …`: first 4 bytes = table size, big-endian (0x000129F7 = 76279) |
| 2. Select a range | `0x01FD3FE0` | data, 8 bytes | length (u32 BE), offset (u32 BE) |
| 3. Request the range | `0x01FDBFE0` | RTR | |
| Answer | `0x09FDBFE0` | data, many frames × 8 bytes | the requested bytes in order |
| After the last range | `0x09FDFFE0` | data, 4 bytes | **CRC32 of the whole table** ("list end") |

`km273_dump.h` requests 4096-byte ranges (512 frames each, the ESP32's RX queue holds them all) and logs them as hex lines. `tools/km273_parse.py` reassembles them and checks the CRC32.

Each table entry:

| Field | Size | Meaning |
|---|---|---|
| idx | u16 | parameter index |
| extid | 7 bytes | internal ID |
| max | s32 | maximum raw value |
| min | s32 | minimum raw value |
| len | s8 | length of the name |
| text | `len` bytes, 0-terminated | parameter name, e.g. `GT3_TEMP` |

### Indices differ between software versions

The FHEM module contains a hard-coded table from 2017. On the reference pump, **1573 of 1792 indices** differ from it, mostly by +1, because the newer software inserted parameters. That is why this project always takes the indices from the pump's own table. Look parameters up **by name**, not by number.

## Broadcast frames

The boards broadcast some raw values on their own, about 2–3 frames/s in total. They are what the listen-only firmware sees. Mapping from the FHEM thread, checked on the reference pump:

| CAN ID | Sensor |
|---|---|
| `08000270` | GT1: heating flow (after the diverter valve) |
| `08004270` | GT2: outdoor |
| `08008270` | GT3: hot water tank |
| `0800C270` | `03FF` = no sensor |
| `08010270` | GT6: hot gas |
| `08014270` | GT8: heat pump flow (condenser out) |
| `08018270` | GT9: heat pump return |
| `0801C270` | GT10: brine in |
| `08020270` | GT11: brine out |

These are **raw NTC ADC values**, not °C. FHEM converts them with a 5th-order polynomial (x = raw value):

```
°C = -5.63141e-13·x⁵ + 1.53737e-09·x⁴ - 1.7745e-06·x³ + 0.00108586·x² - 0.4378966·x + 116.88035
```

Polling the `GTx_TEMP` parameters gives the same temperatures (within ≈ 0.4 K) already in 0.1 °C, which is why the monitor firmware polls instead of decoding broadcasts. The 1-byte 0/1 frames (`00028270` … `00054270`, `08058270` … `0807C270`) are not mapped yet.

## Useful parameters (reference pump)

| Name | Meaning | Scale |
|---|---|---|
| `HW_COMPRESSOR` | Compressor running (0/1) | 1 |
| `HW_VXV` | Diverter valve: 0 = heating, 1 = hot water | 1 |
| `GT1_TEMP` … `GT11_TEMP` | Sensors as in the table above | 0.1 °C |
| `HEATING_CURVE_VALUE` | Current flow setpoint | 0.1 °C |
| `HEATING_START` / `HEATING_STOP` | Current heating start/stop thresholds (dynamic) | 0.1 °C |
| `DHW_GT3_START_TEMP` / `DHW_GT3_STOP_TEMP` | Current hot water thresholds | 0.1 °C |
| `DHW_GT8_STOP_TEMP` | Active hot water stop at GT8 | 0.1 °C |
| `DHW_CALCULATED_SETPOINT_TEMP` | Menu "Warmwassertemperatur" | 0.1 °C |
| `DHW_CALCULATED_SETPOINT_TEMP_OFFSET` | Display offset (factory +4 K) | 0.1 K |
| `STATS_ENERGY_OUTPUT_HEATING` / `_DHW` | Heat produced, lifetime | 0.01 kWh |
| `STATS_COMPRESSOR_HEATING` / `_DHW` | Compressor runtime | s |
| `STATS_COMPRESSOR_START_HEATING` / `_DHW` | Compressor starts | 1 |
| `HW_PUMP_G2_ANALOG` | Heat carrier pump speed | % |

The WPS has **no electrical power measurement**. The current/voltage/frequency parameters read 0.

Next: [6 · Pump behaviour](6-pump-behaviour.md)
