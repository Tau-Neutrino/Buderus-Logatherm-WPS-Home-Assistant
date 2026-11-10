#pragma once
// Buderus Logatherm WPS (KM273 protocol) – bus sniffer and element table dumper.
// Protocol details taken from the FHEM module 26_KM273.pm (mike3436).
//
// The table is not kept on the ESP32. Each received chunk is logged as numbered hex lines
// ("KM273HEX;<offset>;<hex>") and a final "KM273DUMP done" line carries the byte count and CRC32,
// so tools/km273_parse.py can reassemble and verify it on the PC.

#include "esphome.h"
#include <algorithm>
#include <map>
#include <string>
#include <vector>

namespace km273 {

static const char *const TAG = "km273";

// Element table transfer (same sequence a KM200 gateway uses)
static constexpr uint32_t ID_SIZE_REQ = 0x01FD7FE0;   // RTR -> total table size
static constexpr uint32_t ID_SIZE_RSP = 0x09FD7FE0;
static constexpr uint32_t ID_RANGE_REQ = 0x01FD3FE0;  // data: length (u32 BE), offset (u32 BE)
static constexpr uint32_t ID_DATA_REQ = 0x01FDBFE0;   // RTR -> pump streams the requested range
static constexpr uint32_t ID_DATA_RSP = 0x09FDBFE0;
static constexpr uint32_t ID_LIST_END = 0x09FDFFE0;   // seen in FHEM logs after the last range ("Listenende")

static constexpr uint32_t FIRST_FRAME_TIMEOUT_MS = 1000;  // request sent, nothing received yet
static constexpr uint32_t IDLE_TIMEOUT_MS = 300;          // stream started, then went quiet
static constexpr uint32_t MAX_RETRIES_PER_STEP = 10;
static constexpr uint32_t HEX_LINE_BYTES = 64;
static constexpr uint32_t HEX_LINES_PER_TICK = 2;  // paced so the logger/API does not drop lines

inline std::string to_hex(const std::vector<uint8_t> &data) {
  std::string s;
  char b[4];
  for (size_t i = 0; i < data.size(); i++) {
    snprintf(b, sizeof(b), i ? " %02X" : "%02X", data[i]);
    s += b;
  }
  return s;
}

inline uint32_t crc32_update(uint32_t crc, const uint8_t *p, size_t n) {
  crc = ~crc;
  while (n--) {
    crc ^= *p++;
    for (int k = 0; k < 8; k++)
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

class TableDumper {
 public:
  void attach(esphome::canbus::Canbus *bus, uint32_t chunk_size) {
    this->bus_ = bus;
    this->chunk_size_ = chunk_size == 0 ? 4096 : chunk_size;
    bus->add_callback([this](uint32_t can_id, bool ext, bool rtr, const std::vector<uint8_t> &data) {
      this->on_frame_(can_id, ext, rtr, data);
    });
  }

  void set_sniff(bool on) {
    this->sniff_ = on;
    this->last_seen_.clear();
  }

  // Passive bus statistics: logs frames/s and a per-ID count for the window since the last call.
  // Returns frames/s for a Home Assistant sensor (NAN for the first, partial window).
  float report_stats() {
    const uint32_t now = esphome::millis();
    const uint32_t elapsed = now - this->window_start_;
    const bool first = this->window_start_ == 0;
    this->window_start_ = now;
    if (first || elapsed == 0) {
      this->window_ids_.clear();
      this->window_frames_ = 0;
      return NAN;
    }
    const float rate = this->window_frames_ * 1000.0f / elapsed;
    ESP_LOGI(TAG, "STATS window=%.1fs frames=%u rate=%.2f/s ids=%u total=%u", elapsed / 1000.0f,
             (unsigned) this->window_frames_, rate, (unsigned) this->window_ids_.size(),
             (unsigned) this->total_frames_);
    for (const auto &kv : this->window_ids_) {
      ESP_LOGI(TAG, "STATS id=%08X%s%s count=%u", (unsigned) (kv.first & 0x1FFFFFFF),
               (kv.first & 0x40000000u) ? "" : " std", (kv.first & 0x80000000u) ? " RTR" : "", (unsigned) kv.second);
    }
    this->window_ids_.clear();
    this->window_frames_ = 0;
    return rate;
  }

  bool busy() const {
    return this->state_ == State::WAIT_SIZE || this->state_ == State::WAIT_CHUNK || this->state_ == State::PRINTING;
  }

  void start() {
    if (this->bus_ == nullptr) {
      ESP_LOGW(TAG, "CAN bus not attached");
      return;
    }
    if (this->busy()) {
      ESP_LOGW(TAG, "Dump already running");
      return;
    }
    this->total_ = 0;
    this->offset_ = 0;
    this->retries_ = 0;
    this->retries_total_ = 0;
    this->last_partial_ = SIZE_MAX;
    this->crc_ = 0;
    this->chunk_.clear();
    this->chunk_.reserve(this->chunk_size_ + 8);
    this->started_at_ = esphome::millis();
    this->saved_loop_interval_ = esphome::App.get_loop_interval();
    esphome::App.set_loop_interval(1);
    ESP_LOGI(TAG, "KM273DUMP start chunk=%u", (unsigned) this->chunk_size_);
    this->request_size_();
  }

  // Call periodically (e.g. every 10 ms) – handles timeouts, retries and paced printing
  void tick() {
    const uint32_t now = esphome::millis();
    switch (this->state_) {
      case State::WAIT_SIZE:
        if (now - this->sent_at_ > FIRST_FRAME_TIMEOUT_MS && this->retry_("size request"))
          this->request_size_();
        break;
      case State::WAIT_CHUNK: {
        if (this->chunk_.size() >= this->expected_) {
          if (this->chunk_.size() > this->expected_)
            ESP_LOGW(TAG, "Pump sent %u bytes, %u requested; extra bytes dropped", (unsigned) this->chunk_.size(),
                     (unsigned) this->expected_);
          this->chunk_.resize(this->expected_);
          this->begin_print_();
          break;
        }
        const bool started = !this->chunk_.empty();
        const uint32_t since = now - (started ? this->last_data_at_ : this->sent_at_);
        if (since <= (started ? IDLE_TIMEOUT_MS : FIRST_FRAME_TIMEOUT_MS))
          break;
        // The size reported by the pump may be slightly too large (see FHEM module). On the last chunk,
        // the same short answer received twice in a row is taken as the real end of the data.
        const bool last = this->offset_ + this->expected_ >= this->total_;
        if (last && started && this->chunk_.size() == this->last_partial_) {
          ESP_LOGW(TAG, "Accepting short final chunk (%u of %u bytes requested)", (unsigned) this->chunk_.size(),
                   (unsigned) this->expected_);
          this->expected_ = this->chunk_.size();
          this->begin_print_();
          break;
        }
        ESP_LOGW(TAG, "Chunk at offset %u incomplete: %u of %u bytes", (unsigned) this->offset_,
                 (unsigned) this->chunk_.size(), (unsigned) this->expected_);
        this->last_partial_ = this->chunk_.size();
        if (this->retry_("chunk"))
          this->request_chunk_();
        break;
      }
      case State::PRINTING:
        this->print_next_();
        break;
      default:
        break;
    }
  }

 protected:
  enum class State { IDLE, WAIT_SIZE, WAIT_CHUNK, PRINTING, DONE, FAILED };

  void on_frame_(uint32_t can_id, bool ext, bool rtr, const std::vector<uint8_t> &data) {
    this->total_frames_++;
    this->window_frames_++;
    this->window_ids_[can_id | (rtr ? 0x80000000u : 0) | (ext ? 0x40000000u : 0)]++;
    if (ext && !rtr && can_id == ID_SIZE_RSP && this->state_ == State::WAIT_SIZE) {
      // FHEM: 7 byte reply, upper 4 bytes = table size
      this->total_ = data.size() >= 4 ? ((uint32_t) data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3] : 0;
      ESP_LOGI(TAG, "KM273DUMP size=%u raw=%s", (unsigned) this->total_, to_hex(data).c_str());
      if (this->total_ == 0 || this->total_ > 200000) {
        this->fail_("implausible table size");
        return;
      }
      this->retries_ = 0;
      this->request_chunk_();
      return;
    }
    if (ext && !rtr && can_id == ID_LIST_END) {
      ESP_LOGI(TAG, "KM273DUMP list_end raw=%s", to_hex(data).c_str());
      return;
    }
    if (ext && !rtr && can_id == ID_DATA_RSP) {
      if (this->state_ == State::WAIT_CHUNK) {
        this->chunk_.insert(this->chunk_.end(), data.begin(), data.end());
        this->last_data_at_ = esphome::millis();
      }
      return;
    }
    if (this->sniff_)
      this->sniff_frame_(can_id, ext, rtr, data);
  }

  void sniff_frame_(uint32_t can_id, bool ext, bool rtr, const std::vector<uint8_t> &data) {
    const uint32_t key = can_id | (rtr ? 0x80000000u : 0) | (ext ? 0x40000000u : 0);
    auto it = this->last_seen_.find(key);
    if (it != this->last_seen_.end() && it->second == data)
      return;  // only log new IDs or changed payloads
    this->last_seen_[key] = data;

    std::string info;
    if (ext && (can_id & 0x3FFF) == 0x3FE0) {
      const uint32_t idx = (can_id >> 14) & 0x0FFF;
      const uint32_t kind = can_id >> 26;  // 1 = read request / write, 3 = value response
      char buf[48];
      snprintf(buf, sizeof(buf), " idx=%u %s", (unsigned) idx,
               kind == 1 ? (rtr ? "read-req" : "WRITE") : (kind == 3 ? "value" : "?"));
      info = buf;
      if (kind == 3 && data.size() == 2) {
        const int16_t v = (int16_t) ((data[0] << 8) | data[1]);
        snprintf(buf, sizeof(buf), " int=%d", v);
        info += buf;
      }
    }
    ESP_LOGI(TAG, "RX %c %08X [%u] %s%s", rtr ? 'R' : 'T', (unsigned) can_id, (unsigned) data.size(),
             to_hex(data).c_str(), info.c_str());
  }

  void request_size_() {
    this->state_ = State::WAIT_SIZE;
    this->sent_at_ = esphome::millis();
    this->bus_->send_data(ID_SIZE_REQ, true, true, std::vector<uint8_t>{});
  }

  void request_chunk_() {
    this->expected_ = std::min(this->chunk_size_, this->total_ - this->offset_);
    this->chunk_.clear();
    const uint32_t len = this->expected_, off = this->offset_;
    std::vector<uint8_t> range = {(uint8_t) (len >> 24), (uint8_t) (len >> 16), (uint8_t) (len >> 8), (uint8_t) len,
                                  (uint8_t) (off >> 24), (uint8_t) (off >> 16), (uint8_t) (off >> 8), (uint8_t) off};
    this->state_ = State::WAIT_CHUNK;
    this->sent_at_ = esphome::millis();
    this->bus_->send_data(ID_RANGE_REQ, true, false, range);
    this->bus_->send_data(ID_DATA_REQ, true, true, std::vector<uint8_t>{});
  }

  void begin_print_() {
    this->retries_ = 0;
    this->last_partial_ = SIZE_MAX;
    this->print_pos_ = 0;
    this->state_ = State::PRINTING;
  }

  void print_next_() {
    char line[HEX_LINE_BYTES * 2 + 1];
    for (uint32_t n = 0; n < HEX_LINES_PER_TICK && this->print_pos_ < this->chunk_.size(); n++) {
      const size_t count = std::min<size_t>(HEX_LINE_BYTES, this->chunk_.size() - this->print_pos_);
      for (size_t k = 0; k < count; k++)
        snprintf(line + 2 * k, 3, "%02X", this->chunk_[this->print_pos_ + k]);
      ESP_LOGI(TAG, "KM273HEX;%06X;%s", (unsigned) (this->offset_ + this->print_pos_), line);
      this->print_pos_ += count;
    }
    if (this->print_pos_ < this->chunk_.size())
      return;

    this->crc_ = crc32_update(this->crc_, this->chunk_.data(), this->chunk_.size());
    this->offset_ += this->chunk_.size();
    ESP_LOGI(TAG, "KM273DUMP progress %u/%u retries=%u", (unsigned) this->offset_, (unsigned) this->total_,
             (unsigned) this->retries_total_);
    if (this->offset_ >= this->total_ || this->chunk_.size() < this->chunk_size_)
      this->finish_();
    else
      this->request_chunk_();
  }

  bool retry_(const char *what) {
    this->retries_++;
    this->retries_total_++;
    if (this->retries_ > MAX_RETRIES_PER_STEP) {
      this->fail_(what);
      return false;
    }
    return true;
  }

  void fail_(const char *why) {
    ESP_LOGE(TAG, "KM273DUMP failed: %s (offset %u of %u)", why, (unsigned) this->offset_, (unsigned) this->total_);
    this->state_ = State::FAILED;
    this->chunk_ = std::vector<uint8_t>();
    esphome::App.set_loop_interval(this->saved_loop_interval_);
  }

  void finish_() {
    esphome::App.set_loop_interval(this->saved_loop_interval_);
    ESP_LOGI(TAG, "KM273DUMP done bytes=%u size=%u crc32=%08X retries=%u time_ms=%u", (unsigned) this->offset_,
             (unsigned) this->total_, (unsigned) this->crc_, (unsigned) this->retries_total_,
             (unsigned) (esphome::millis() - this->started_at_));
    this->state_ = State::DONE;
    this->chunk_ = std::vector<uint8_t>();
  }

  esphome::canbus::Canbus *bus_{nullptr};
  State state_{State::IDLE};
  bool sniff_{false};
  uint32_t chunk_size_{4096};
  uint32_t total_{0};
  uint32_t offset_{0};
  uint32_t expected_{0};
  uint32_t sent_at_{0};
  uint32_t last_data_at_{0};
  uint32_t started_at_{0};
  uint32_t retries_{0};
  uint32_t retries_total_{0};
  uint32_t crc_{0};
  size_t last_partial_{SIZE_MAX};
  size_t print_pos_{0};
  uint32_t saved_loop_interval_{16};
  std::vector<uint8_t> chunk_;
  std::map<uint32_t, std::vector<uint8_t>> last_seen_;
  uint32_t total_frames_{0};
  uint32_t window_frames_{0};
  uint32_t window_start_{0};
  std::map<uint32_t, uint32_t> window_ids_;
};

static TableDumper dumper;

}  // namespace km273
