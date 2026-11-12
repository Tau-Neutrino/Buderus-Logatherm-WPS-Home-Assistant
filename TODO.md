# TODO

- [x] **Power consumption:** Shelly Pro 3EM installed (2026-11-10), package `homeassistant/wps-energy-package.yaml` (tariff split, COP today / per run), cards on the WPS page.
- [ ] Verify the measured COP after a few days of runs: compare with the estimated COP and check the run-COP mode detection.
- [ ] Check with the Shelly per-phase sensors whether the electric heater shows up; afterwards disable them again to keep the database small.
- [x] `HW_COMPRESSOR` confirmed (2026-11-09, heating run: HW_COMPRESSOR, COMPRESSOR_STATE, COMPRESSOR_START all 0→1). `HW_VXV` = 0 while heating confirmed.
- [x] `HW_VXV` = 1 during hot water confirmed (2026-11-09 21:01, DHW_REQUEST 1, HEATING_REQUEST 0).
- [x] Pumps (2026-11-09): G1 = heating circuit (on in both states), G2 = heat carrier, variable speed (37 % idle → 71 % running), G3 = brine pump (only with compressor).
- [ ] Unit of `STATS_ELECTR_ADD_*` (electric heater counters).
- [ ] Heating curve chart assumes adjustment Y1 = +20 °C … Y8 = −15 °C (all 0 today). If a single point is ever adjusted on the display, check that the chart bends at the right place.
- [x] Final polling intervals (2026-11-09): only the 37 parameters used on the heat pump page, 10 s … 15 min per parameter (`interval_s` in tools/monitor_params.csv).
- [ ] Median COP heating / hot water (HA statistics helper) and overall COP = ΣQ / Σ(Q/COP) - proposed, not decided.
- [ ] Writing parameters (e.g. hot water settings) from Home Assistant: only after the menu → parameter mapping is proven, with an allowlist and read-back.
