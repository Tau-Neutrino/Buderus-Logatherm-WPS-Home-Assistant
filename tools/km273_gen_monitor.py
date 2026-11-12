"""Generate the monitor firmware from tools/monitor_params.csv.

Usage:
    python tools/km273_gen_monitor.py

Writes esphome/buderus-wps-monitor.yaml. Each parameter is polled at its own interval (column interval_s).
Indexes come from km273_dictionary.csv (this pump's dumped element table plus our notes).
Other pump/software version: dump your own table and pass it with --elements (see docs/3-firmware.md).

To add a parameter: pick it in km273_dictionary.csv, add a row to tools/monitor_params.csv
(the label becomes the Home Assistant entity name - keep labels stable to keep the history).
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVICE = "Buderus WPS"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elements", help="element table of YOUR pump (CSV written by tools/km273_parse.py); "
                    "its indices replace the ones in km273_dictionary.csv, matched by parameter name")
    args = ap.parse_args()

    with open(ROOT / "km273_dictionary.csv", encoding="utf-8") as fh:
        table = {r["name"]: r for r in csv.DictReader(fh, delimiter=";")}
    if args.elements:
        with open(args.elements, encoding="utf-8") as fh:
            own = {r["text"]: r["idx"] for r in csv.DictReader(fh, delimiter=";")}
        table = {name: {**row, "idx": own[name]} for name, row in table.items() if name in own}
        print(f"Using indices from {args.elements} ({len(own)} elements).")
    with open(ROOT / "tools" / "monitor_params.csv", encoding="utf-8") as fh:
        params = list(csv.DictReader(fh, delimiter=";"))
    missing = [p["param"] for p in params if p["param"] not in table]
    if missing:
        sys.exit(f"Not in this pump's table: {missing}")

    sensors, adds = [], []
    for p in params:
        sid = "p_" + slug(p["param"])
        lines = [
            "  - platform: template",
            f"    id: {sid}",
            f'    name: "{p["label"]}"',
            "    update_interval: never",
            f"    accuracy_decimals: {p['decimals']}",
        ]
        if p["unit"]:
            lines.append(f'    unit_of_measurement: "{p["unit"]}"')
        if p["unit"] == "°C":
            lines += ["    device_class: temperature", "    state_class: measurement"]
        elif p["unit"] == "kWh":
            lines += ["    device_class: energy", "    state_class: total_increasing"]
        elif p["group"] == "Statistics":
            lines.append("    state_class: total_increasing")
        else:
            lines.append("    state_class: measurement")
        sensors.append("\n".join(lines))
        interval_ms = int(float(p["interval_s"]) * 1000)
        adds.append(f"          km273::poller.add({table[p['param']]['idx']}, {float(p['scale'])!r}f, id({sid}), {interval_ms});")

    # Values calculated on the ESP32 from polled parameters (same numbers as the pump's display).
    derived = [
        ("Hot water (display)", "°C", "id(p_gt3_temp).state + id(p_dhw_calculated_setpoint_temp_offset).state"),
        ("Heating curve left point (display)", "°C",
         "id(p_heating_curve_left_y_local).state + id(p_heating_curve_parallel_offset).state"),
        ("Heating curve right point (display)", "°C",
         "id(p_heating_curve_right_y_local).state + id(p_heating_curve_parallel_offset).state"),
    ]
    for label, unit, expr in derived:
        sensors.append("\n".join([
            "  - platform: template",
            f'    name: "{label}"',
            f'    unit_of_measurement: "{unit}"',
            "    device_class: temperature",
            "    state_class: measurement",
            "    accuracy_decimals: 1",
            "    update_interval: 10s",
            f"    lambda: return {expr};",
        ]))
    # Estimated COP from the datasheet (km273_cop.h); NAN when the compressor is off or just started,
    # so Home Assistant's long-term mean only covers running time. Per-mode variants split by diverter valve.
    cop_expr = "km273::cop_estimate(id(p_hw_compressor).state, id(p_hw_vxv).state, id(p_gt10_temp).state, id(p_gt8_temp).state)"
    vxv = "id(p_hw_vxv).state"
    cops = [
        ("Estimated COP", f"return {cop_expr};"),
        ("Estimated COP heating", f"const float c = {cop_expr}; return ({vxv} < 0.5f) ? c : NAN;"),
        ("Estimated COP hot water", f"const float c = {cop_expr}; return ({vxv} > 0.5f) ? c : NAN;"),
    ]
    for label, body in cops:
        sensors.append("\n".join([
            "  - platform: template",
            f'    name: "{label}"',
            "    icon: mdi:gauge",
            "    state_class: measurement",
            "    accuracy_decimals: 2",
            "    update_interval: 10s",
            "    lambda: |-",
            f"      {body}",
        ]))
    extra = """
text_sensor:
  - platform: template
    name: "Operating mode"
    icon: mdi:heat-pump
    update_interval: 10s
    lambda: |-
      const float c = id(p_hw_compressor).state, v = id(p_hw_vxv).state;
      if (std::isnan(c)) return {"${mode_unknown}"};
      if (c < 0.5f) return {"${mode_standby}"};
      return {(!std::isnan(v) && v > 0.5f) ? "${mode_hot_water}" : "${mode_heating}"};

  # Flow with its setpoint for the "Flow" tile, e.g. "25.4 °C (set: 28.2 °C)".
  - platform: template
    name: "Flow with setpoint"
    icon: mdi:radiator
    update_interval: 10s
    lambda: |-
      const float t = id(p_gt1_temp).state, s = id(p_heating_curve_value).state;
      if (std::isnan(t)) return {"—"};
      char buf[48];
      if (std::isnan(s))
        snprintf(buf, sizeof(buf), "%.1f °C", t);
      else
        snprintf(buf, sizeof(buf), "%.1f °C (${setpoint_label}: %.1f °C)", t, s);
      std::string out(buf);
      for (auto &ch : out)
        if (ch == '.') ch = '${decimal_separator}';
      return out;

binary_sensor:
  - platform: template
    name: "Compressor running"
    device_class: running
    lambda: return id(p_hw_compressor).state > 0.5f;
"""

    firmware = f"""# GENERATED by tools/km273_gen_monitor.py from tools/monitor_params.csv - edit those, not this file.
# Monitor firmware: polls {len(params)} parameters, each at its own interval (read requests only, no writes).
substitutions:
  name: buderus-wps
  friendly_name: {DEVICE}
  # Texts of the "Operating mode" and "Flow with setpoint" sensors. Override them for another language,
  # e.g. esphome -s mode_heating Heizen -s mode_hot_water Warmwasser ... run buderus-wps-monitor.yaml
  mode_standby: Standby
  mode_heating: Heating
  mode_hot_water: Hot water
  mode_unknown: Unknown
  setpoint_label: set
  decimal_separator: "."

esphome:
  name: ${{name}}
  friendly_name: ${{friendly_name}}
  includes:
    - km273_dump.h
    - km273_poller.h
    - km273_cop.h
  on_boot:
    priority: -100
    then:
      - lambda: |-
          km273::dumper.attach(id(can0), 4096);
          km273::poller.attach(id(can0));
{chr(10).join(adds)}

esp32:
  board: esp32dev
  framework:
    type: esp-idf

logger:
  level: DEBUG
  baud_rate: 115200
  logs:
    canbus: INFO

api:
  encryption:
    key: !secret api_encryption_key

ota:
  - platform: esphome
    password: !secret ota_password

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  ap:
    password: !secret fallback_ap_password

captive_portal:

canbus:
  - platform: esp32_can
    id: can0
    rx_pin: GPIO21
    tx_pin: GPIO22
    bit_rate: 125KBPS
    mode: NORMAL
    rx_queue_len: 64
    can_id: 0x01FD7FE0
    use_extended_id: true

switch:
  - platform: template
    name: CAN sniffer log
    icon: mdi:magnify-scan
    entity_category: diagnostic
    optimistic: true
    restore_mode: ALWAYS_OFF
    turn_on_action:
      - lambda: km273::dumper.set_sniff(true);
    turn_off_action:
      - lambda: km273::dumper.set_sniff(false);

button:
  - platform: restart
    name: Restart

interval:
  - interval: 5ms
    then:
      - lambda: |-
          km273::dumper.tick();
          km273::poller.tick();

sensor:
  - platform: template
    name: CAN frame rate
    icon: mdi:swap-horizontal
    unit_of_measurement: frames/s
    state_class: measurement
    accuracy_decimals: 2
    entity_category: diagnostic
    update_interval: 30s
    lambda: return km273::dumper.report_stats();
{chr(10).join(sensors)}
{extra}"""
    out_fw = ROOT / "esphome" / "buderus-wps-monitor.yaml"
    out_fw.write_text(firmware, encoding="utf-8", newline="\n")

    print(f"Wrote {out_fw.relative_to(ROOT)} ({len(params)} polled parameters)")


if __name__ == "__main__":
    main()
