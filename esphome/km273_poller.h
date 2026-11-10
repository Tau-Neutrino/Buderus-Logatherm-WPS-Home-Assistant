#pragma once
// Cyclic parameter reader (KM273 protocol, see FHEM 26_KM273).
// Read request: RTR on 0x04003FE0 | (idx << 14); value reply on 0x0C003FE0 | (idx << 14), big-endian.
// One request at a time; the parameter most overdue for its own interval is read next. 2-byte values are signed,
// 0xDEAD means "no sensor" and is published as NAN.

#include "esphome.h"

#include <cmath>
#include <vector>

namespace km273 {

class Poller {
 public:
  void attach(esphome::canbus::Canbus *bus) {
    this->bus_ = bus;
    bus->add_callback([this](uint32_t id, bool ext, bool rtr, const std::vector<uint8_t> &data) {
      if (ext && !rtr && this->waiting_ && id == this->expect_id_)
        this->on_reply_(data);
    });
  }

  // interval_ms: how often this parameter is read.
  void add(uint16_t idx, float scale, esphome::sensor::Sensor *sensor, uint32_t interval_ms) {
    this->entries_.push_back({idx, scale, sensor, interval_ms, 0, false, 0});
  }

  // Call every few ms.
  void tick() {
    if (this->bus_ == nullptr || this->entries_.empty())
      return;
    const uint32_t now = esphome::millis();
    if (this->waiting_) {
      if (now - this->sent_at_ < REPLY_TIMEOUT_MS)
        return;
      Entry &e = this->entries_[this->current_];
      this->waiting_ = false;
      this->last_request_ = now;
      // Count the attempt so a silent parameter waits its interval instead of starving the others.
      e.last_read = now;
      e.read_once = true;
      if (++e.misses == MISSES_BEFORE_UNKNOWN)
        e.sensor->publish_state(NAN);
      if (e.misses == 1 || e.misses % 10 == 0)
        ESP_LOGW("km273", "No reply for idx %u (%u misses)", (unsigned) e.idx, (unsigned) e.misses);
    }
    if (now - this->last_request_ < REQUEST_GAP_MS)
      return;

    // Entry most overdue relative to its own interval; entries never read come first.
    size_t best = SIZE_MAX;
    uint32_t best_overdue = 0;
    for (size_t i = 0; i < this->entries_.size(); i++) {
      const Entry &e = this->entries_[i];
      const uint32_t age = e.read_once ? now - e.last_read : UINT32_MAX;
      if (age < e.interval_ms)
        continue;
      const uint32_t overdue = e.read_once ? age - e.interval_ms : UINT32_MAX;
      if (best == SIZE_MAX || overdue > best_overdue) {
        best = i;
        best_overdue = overdue;
      }
    }
    if (best == SIZE_MAX)
      return;
    this->current_ = best;
    Entry &e = this->entries_[best];
    this->expect_id_ = 0x0C003FE0u | ((uint32_t) e.idx << 14);
    this->waiting_ = true;
    this->sent_at_ = now;
    this->bus_->send_data(0x04003FE0u | ((uint32_t) e.idx << 14), true, true, std::vector<uint8_t>{});
  }

 protected:
  static constexpr uint32_t REPLY_TIMEOUT_MS = 300;
  static constexpr uint32_t REQUEST_GAP_MS = 50;
  static constexpr uint32_t MISSES_BEFORE_UNKNOWN = 3;

  struct Entry {
    uint16_t idx;
    float scale;
    esphome::sensor::Sensor *sensor;
    uint32_t interval_ms;
    uint32_t last_read;
    bool read_once;
    uint32_t misses;
  };

  void on_reply_(const std::vector<uint8_t> &d) {
    Entry &e = this->entries_[this->current_];
    const uint32_t now = esphome::millis();
    this->waiting_ = false;
    this->last_request_ = now;
    e.last_read = now;
    e.read_once = true;
    e.misses = 0;

    uint32_t u = 0;
    for (uint8_t b : d)
      u = (u << 8) | b;
    float value;
    if (d.size() == 2 && u == 0xDEAD)
      value = NAN;
    else if (d.size() == 2)
      value = (int16_t) u * e.scale;
    else if (d.size() == 4)
      value = (float) ((double) (int32_t) u * e.scale);
    else
      value = u * e.scale;
    e.sensor->publish_state(value);
  }

  esphome::canbus::Canbus *bus_{nullptr};
  std::vector<Entry> entries_;
  bool waiting_{false};
  size_t current_{0};
  uint32_t expect_id_{0};
  uint32_t sent_at_{0};
  uint32_t last_request_{0};
};

static Poller poller;

}  // namespace km273
