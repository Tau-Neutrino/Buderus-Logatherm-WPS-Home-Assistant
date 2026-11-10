# 6 · Pump behaviour (what the data shows)

Findings from the first days of logging on a WPS 8K-1. They explain several "strange" values and help with tuning. Parameter names refer to `km273_dictionary.csv`.

## Display vs. parameters

| Display shows | Parameters |
|---|---|
| Hot water temperature | `GT3_TEMP` **+ 4 K** (`DHW_CALCULATED_SETPOINT_TEMP_OFFSET`, menu "Verstellen der berechneten Warmwassertemperatur") |
| Heating curve points | `HEATING_CURVE_LEFT/RIGHT_Y_LOCAL` **+** `HEATING_CURVE_PARALLEL_OFFSET` |

The firmware therefore provides **Hot water (display)** and **Heating curve … (display)** sensors. When comparing with the display, keep in mind that during a hot water charge GT3 rises ≈ 1 K/min, so a 30 s polling interval can lag the display by ≈ 0.5 K.

## Heating: start and stop

- `HEATING_CURVE_VALUE` is the flow setpoint (heating curve at the current outdoor temperature). GT1 (heating flow) follows it.
- The compressor starts and stops on `HEATING_START` / `HEATING_STOP`, which lie symmetrically around the setpoint.
- This band is **dynamic**: after a compressor start it resets wide (≈ ±12 K) and shrinks by ≈ 0.6 K/min while running, and only ≈ 0.03 K/min while idle.
- Pumps: G1 heating circuit (runs in standby and operation), G2 heat carrier (variable speed, ≈ 37 % idle → 71 % running), G3 brine (only with the compressor).

## Hot water: start and stop

Two sensors decide:

| Sensor | Where | Role |
|---|---|---|
| **GT3** | in the hot water tank | **start** of a charge |
| **GT8** | heat pump flow (into the tank coil) | **stop** of a charge |

**Start: GT3 falls to the start threshold `DHW_GT3_START_TEMP`.** The threshold is not fixed:

- After each charge it resets to **40.0 °C** (`DHW_CALCULATED_T3_START_TEMP_MIN_VALUE` = 400).
- It then rises by 0.1 K every **360 s** (`DHW_CALCULATED_E21_T3_START_TEMP_SEC_PER_TENTH_ADJ`), i.e. 1 K/h.
- The rate = (GT3 target − 40 °C) ÷ **loading interval** (`DHW_CALCULATED_T3_DHW_LOADING_INTERVAL` = 6 h). So the tank is recharged after 6 h at the latest, even without any hot water use.
- It never rises above the menu value **"Maximale Starttemperatur T3"** (`DHW_GT3_START_MAX_TEMP`, factory 53 °C).

**Stop: GT8 reaches "Warmwassertemperatur" + 4 K** (`DHW_GT8_STOP_TEMP`; at 50 °C → 54 °C), limited by the menu value **"Maximale Stopptemperatur T8"** (`DHW_GT8_STOP_MAX_TEMP`, factory 61 °C). There is also a GT3 stop value (`DHW_GT3_STOP_TEMP` = Warmwassertemperatur − 4 + 0.5), but in all observed charges GT3 passed it minutes before the charge ended. In practice GT8 ends the charge: when the tank around the coil is warm, the flow temperature rises quickly.

**The menu links the two:** "Warmwassertemperatur" can be at most "Maximale Starttemperatur T3" + 4 K. Lowering the start maximum also pushes the hot water temperature down.

### What that looks like

| Start (local) | Compressor before | GT3 at start | Start threshold | Run length |
|---|---|---|---|---|
| 21:01 | heating | 42.8 | 42.4 | 6.8 min |
| 22:38 | off | 41.6 | 41.5 | **29.9 min** (after hot water use: GT3 fell 2.6 K in 3 min) |
| 23:23 | heating | 40.3 | 40.2 | 8.3 min |
| 01:07 | off | 41.6 | 41.5 | 6.2 min |
| 05:02 | heating | 43.8 | 43.7 | 5.7 min |
| 09:32 | off | 44.4 | 44.3 | 5.5 min |

- After **real hot water use**, cold water refills the tank, GT3 drops fast and the charge is long.
- Without use, the rising threshold meets the slowly cooling tank after ≈ 4 h and the pump does a **short 5–6 min top-up** of a tank that is still mostly hot.
- The lifetime counters of the reference pump show the same: more hot water starts (12 921) than heating starts (9 933), ≈ 10 min per hot water run.

### Tuning (installer level)

The installer level opens with: standard screen → hold **MODE + INFO** → press **MENU** once → release all (manual chapter 13.1). Change a value: select it, **hold MENU** and turn the knob, release to save.

| Goal | Setting | Trade-off |
|---|---|---|
| Longer charges, fewer top-ups | raise **Warmwassertemperatur** (raises the GT8 stop) | lower COP for hot water (≈ 0.1 per K) |
| Fewer top-ups | lower **Maximale Starttemperatur T3** below ≈ 44 °C | also caps Warmwassertemperatur at that value + 4 |
| Avoid inefficient high flow temperatures | lower **Maximale Stopptemperatur T8**, e.g. to 55 °C | none, as long as it is above Warmwassertemperatur + 4 |

The 6 h loading interval is not in the installer menu.

## COP estimate

`km273_cop.h` fits the datasheet curves (COP over brine temperature for flow 35/45/55 °C):

```
COP = 10.9125 − 0.23·Tf + 0.0015·Tf² + (0.225 − 0.003·Tf)·Ts      Ts = brine in (GT10), Tf = heat pump flow (GT8)
```

It is only published while the compressor runs and from 60 s after a start or a heating↔hot water switch. By then GT8 has settled for heating, and during hot water it has reached ≈ 44 °C and keeps rising with the tank, which is real. In the first 2–3 minutes the brine is still warm from standing, so the first values read ≈ 0.3 high.

## Energy counters

- `STATS_ENERGY_OUTPUT_HEATING` + `_DHW` (raw × 0.01 kWh) add up to the total, and the averages match the 8 kW rating.
- The pump only reports produced heat. For electricity and a real COP, add a meter in the supply.
