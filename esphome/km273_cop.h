#pragma once
// Estimated COP of the Buderus WPS 8K-1 from the datasheet chart (COP over brine temperature
// for flow 35/45/55 °C). Fit through the end points of all three curves (exact at the end points,
// about ±0.05 in between):
//   COP = 10.9125 - 0.23*Tf + 0.0015*Tf^2 + (0.225 - 0.003*Tf) * Ts
// Ts = brine in (GT10), Tf = heat pump flow (GT8). Inputs are clamped to the datasheet range.

#include "esphome.h"

#include <algorithm>
#include <cmath>

namespace km273 {

static constexpr uint32_t COP_SETTLE_MS = 60 * 1000;  // GT8 settles within ~1 min after a start or mode switch (HA history 2026-11-10)

inline float cop_formula(float ts, float tf) {
  if (std::isnan(ts) || std::isnan(tf))
    return NAN;
  tf = std::min(55.0f, std::max(35.0f, tf));
  ts = std::min(20.0f, std::max(-5.0f, ts));
  return 10.9125f - 0.23f * tf + 0.0015f * tf * tf + (0.225f - 0.003f * tf) * ts;
}

// NAN unless the compressor has been running in the same mode (diverter valve) for COP_SETTLE_MS.
// A switch between heating and hot water restarts the settling time, because GT8 needs a few
// minutes to reach the new level.
inline float cop_estimate(float compressor, float valve, float ts, float tf) {
  static bool running = false;
  static bool hot_water = false;
  static uint32_t since = 0;
  const uint32_t now = esphome::millis();
  if (std::isnan(compressor) || compressor < 0.5f) {
    running = false;
    return NAN;
  }
  const bool hw = !std::isnan(valve) && valve > 0.5f;
  if (!running || hw != hot_water) {
    running = true;
    hot_water = hw;
    since = now;
  }
  if (now - since < COP_SETTLE_MS)
    return NAN;
  return cop_formula(ts, tf);
}

}  // namespace km273
