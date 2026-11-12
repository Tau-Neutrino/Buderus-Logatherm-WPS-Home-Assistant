# 4 · Home Assistant

## Adding the device

1. Once the monitor firmware runs, Home Assistant discovers it automatically: **Settings → Devices & services → ESPHome → "Buderus WPS" → Configure**.
2. Enter the `api_encryption_key` from `esphome/secrets.yaml`.
3. Optionally assign an **area**. Note: this changes nothing about the entity IDs that were already created, but entities created *after* assigning the area may get the area as prefix. The reference installation has the area "Haus", so its entities are called `sensor.haus_buderus_wps_…`. Without an area they are `sensor.buderus_wps_…`.

Find out which prefix you have:

```bash
python tools/ha_entities.py list buderus_wps
```

```
sensor.haus_buderus_wps_hot_water_gt3        45.2   esphome   Hot water GT3
sensor.haus_buderus_wps_operating_mode       Heating esphome  Operating mode
...
```

Or in the UI: **Settings → Entities**, search "buderus".

## Entities (examples)

The entity ID is derived from the `label` in `tools/monitor_params.csv`:

| Label | Entity ID (prefix `sensor.<area>_buderus_wps_`) | Example |
|---|---|---|
| Operating mode | `…operating_mode` | `Heating` / `Hot water` / `Standby` |
| Compressor running | `binary_sensor.…compressor_running` | `on` |
| Heating flow GT1 | `…heating_flow_gt1` | 31.4 °C |
| Flow setpoint | `…flow_setpoint` | 30.9 °C |
| Outdoor GT2 | `…outdoor_gt2` | 6.1 °C |
| Hot water (display) | `…hot_water_display` | 49.2 °C (as on the pump display) |
| Hot water GT3 | `…hot_water_gt3` | 45.2 °C (raw tank sensor) |
| Brine in GT10 / out GT11 | `…brine_in_gt10`, `…brine_out_gt11` | 8.6 / 5.9 °C |
| Heat produced heating / hot water | `…heat_produced_heating`, `…heat_produced_hot_water` | 70851 kWh (lifetime counters, usable in the energy dashboard) |
| Compressor starts heating / hot water | `…compressor_starts_heating`, … | 9933 |
| Estimated COP | `…estimated_cop` | 5.6 |

## The dashboard

[`homeassistant/wps-dashboard-view.yaml`](../homeassistant/wps-dashboard-view.yaml) is a complete dashboard **view** (type *sections*):

| Section | Content |
|---|---|
| **Current status** | Tiles: compressor, operating mode, flow with setpoint, outdoor temperature. Hot water **gauge** with start/stop range and setpoint. |
| **Energy** | Donut of the produced heat (heating vs. hot water, lifetime), a second donut for the **last 24 h**, heat and electricity **per day** |
| **COP estimated (datasheet curves)** | COP estimated from the datasheet curves (brine in GT10, flow GT8): current value and mean per day |
| **COP measured** | Needs an electricity meter and the package below: power, electricity today, COP of the last run / today / per mode, measured COP per day |
| **Heating curve** | The heating curve as configured in the pump, with today's operating point |
| **Last 24 hours** | History graph of the temperatures; **Operating data**: compressor runtime and starts, operating mode over 24 h |

Screenshots of the reference installation (phone, dark theme):

| Status, hot water, energy | Heat 24 h and per day | Estimated and measured COP | Heating curve, operating data |
|---|---|---|---|
| <img src="Pictures/HA_example_1.png" alt="Status, hot water gauge, lifetime heat donut" width="200"> | <img src="Pictures/HA_example_2.png" alt="Heat produced in the last 24 h, heat and electricity per day" width="200"> | <img src="Pictures/HA_example_3.png" alt="Estimated COP per day, measured power, electricity and COP" width="200"> | <img src="Pictures/HA_example_4.png" alt="Heating curve with switching band, compressor runtimes and starts" width="200"> |
| Compressor, mode, flow with setpoint, brine. Hot water gauge with start/stop range. Heat produced over the pump's lifetime. | Heat produced in the last 24 h; heat and electricity per day. | COP estimated from the datasheet curves; measured power, electricity and COP (last run, today, per mode, per day). | The heating curve with today's operating point and the current start/stop band. Compressor runtimes and starts. (The 24 h history graphs are not shown.) |

### Requirements

- **HACS** with the frontend card **[plotly-graph-card](https://github.com/dbuezas/lovelace-plotly-graph-card)** (v3 or later). All other cards are built in.
- The pump's energy counters must have long-term statistics. That is automatic, because the firmware marks them `total_increasing`. The "per day" bars fill up from the day the sensors were created.

### Installing the view

1. **Adapt the entity prefix.** The file uses `sensor.haus_buderus_wps_` (area "Haus"). If your entities are named differently, replace it everywhere:

   ```bash
   # Linux/macOS: entities without area
   sed -i 's/haus_buderus_wps_/buderus_wps_/g' homeassistant/wps-dashboard-view.yaml
   ```
   ```powershell
   # Windows PowerShell
   (Get-Content homeassistant\wps-dashboard-view.yaml -Raw) -replace 'haus_buderus_wps_','buderus_wps_' | Set-Content -Encoding utf8 homeassistant\wps-dashboard-view.yaml
   ```

2. **Either via the UI:** open a dashboard → ✏ Edit → **+ Add view** → open the new view's settings → ⋮ **Edit in YAML** → replace everything with the file's content → Save.

3. **Or with the script** (replaces the view with the same `path: heat-pump` in an existing dashboard and saves a backup of the whole dashboard to `homeassistant/backups/` first):

   ```bash
   # once: create a long-lived access token (HA profile → Security) and store it
   echo "<token>" > ~/.ha_token                      # Windows: %USERPROFILE%\.ha_token
   export HA_WS_URL=ws://homeassistant.local:8123/api/websocket   # default; set it if your HA has another address

   python tools/ha_dashboard.py views lovelace        # list the views of the default dashboard
   python tools/ha_dashboard.py push lovelace homeassistant/wps-dashboard-view.yaml
   ```

   `lovelace` is the default dashboard; for another dashboard use its URL path. The view must already exist once (step 2 with only `path: heat-pump`), then `push` keeps it up to date.

### A compact card for another page

[`homeassistant/heatpump-status-card.yaml`](../homeassistant/heatpump-status-card.yaml) is an example of a small status block (operating mode, flow, hot water, outdoor temperature) that links to the full view. It needs the HACS cards **button-card** and **card-mod**. Add it with *Add card → Manual*, and adapt the entity prefix as above.

## Examples

### Notification when hot water is low

```yaml
automation:
  - alias: "Hot water low"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.buderus_wps_hot_water_display
        below: 44
        for: "00:15:00"
    actions:
      - action: notify.notify
        data:
          message: "Hot water only {{ states('sensor.buderus_wps_hot_water_display') }} °C"
```

### Count compressor starts per day

```yaml
utility_meter:
  wps_starts_heating_daily:
    source: sensor.buderus_wps_compressor_starts_heating
    cycle: daily
  wps_starts_hot_water_daily:
    source: sensor.buderus_wps_compressor_starts_hot_water
    cycle: daily
```

### Average compressor run time today (template sensor)

```yaml
template:
  - sensor:
      - name: "WPS average run time heating today"
        unit_of_measurement: "min"
        state: >
          {% set starts = states('sensor.wps_starts_heating_daily') | float(0) %}
          {% set hours = states('sensor.wps_runtime_heating_daily') | float(0) %}
          {{ (hours * 60 / starts) | round(1) if starts > 0 else 0 }}
```

(`wps_runtime_heating_daily` is a second `utility_meter` on `sensor.buderus_wps_compressor_runtime_heating`.)

### Energy dashboard

Add `sensor.buderus_wps_heat_produced_heating` and `…_hot_water` under **Settings → Dashboards → Energy** as individual devices to see the heat produced per day/week/month.

### Electricity and measured COP (Shelly Pro 3EM)

With an electricity meter in the pump's supply you get the real COP = heat ÷ electricity. [`homeassistant/wps-energy-package.yaml`](../homeassistant/wps-energy-package.yaml) is a ready-made [package](https://www.home-assistant.io/docs/configuration/packages/) for a Shelly Pro 3EM. Copy it to `/config/packages/`, adjust the entity IDs and restart Home Assistant. It provides:

| Entity | Meaning |
|---|---|
| `sensor.wps_electricity_heating` / `_hot_water` / `_standby` | Lifetime kWh, split by an automation that switches a `utility_meter` tariff based on the compressor and the diverter valve. The source is the integrated Shelly power (updates every ~16 s), not the Shelly kWh counter: that one only updates once a minute, so up to a minute of energy would be booked to the wrong mode at each switch. They can be added to the Energy dashboard. |
| `sensor.wps_cop_heating_today` / `_hot_water_today` | Heat ÷ electricity today while the compressor runs in that mode |
| `sensor.wps_cop_today` | All heat ÷ all electricity today, standby included |
| `sensor.wps_cop_last_run` | COP of the last compressor run (start → 60 s after stop). Attributes: `mode`, `minutes`, `heat_kwh`, `electricity_kwh` |

The heat comes from the pump's own counters (`STATS_ENERGY_OUTPUT_*`, 0.01 kWh, polled every 30 s). The pump has no flow meter that we know of, so the heat figure is probably the pump's own calculation rather than a measurement.

**Database size.** The Shelly reports every few seconds; its per-phase and apparent-power sensors alone wrote ~33,000 rows per day here. Only `…_power` and `…_energy` are used, so disable the rest under **Settings → Entities**. The package also has a `recorder: exclude:` block for the helper counters and two ESP32 diagnostics; it merges with the `recorder:` section of `configuration.yaml` as long as that one has no `exclude:` of its own.


## Helper scripts

| Script | Purpose |
|---|---|
| `tools/ha_entities.py list <text> [--unavailable]` | List entities whose ID contains `<text>`, with state and platform. `--unavailable` shows only orphaned ones, e.g. after renaming labels. |
| `tools/ha_entities.py remove <entity_id> …` | Remove orphaned entities from the registry |
| `tools/ha_dashboard.py views/push …` | See above |

All of them read the token from `~/.ha_token` (never printed) and the address from `HA_WS_URL`.

Next: [5 · Protocol reference](5-protocol.md)
