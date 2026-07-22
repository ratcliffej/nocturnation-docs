# Wire spec v0x03: pulse-sync via `send_tick`

**Status:** design — not implemented
**Target:** protocol version bump `0x02 → 0x03`
**Motivation:** cross-Lume rendering desync under multi-hop repeater deployments
**Prerequisite:** PRs [`stickc#34`](https://github.com/ratcliffej/nocturnation-stickc/pull/34) + [`tildagon#23`](https://github.com/ratcliffej/nocturnation-tildagon/pull/23) merged — those land the Lume-side `director_tick_offset_ms` tracking this spec consumes.

## 1. Problem

Bench observation (2026-07-22): with engineered repeaters relaying to a stage-front fleet, pulses visibly fire out of unison. The impression is that "the first instance of a duplicated frame isn't rendering" — but a code walk confirms the per-badge dedup (`seen_recently → mark_seen → is_dup return`) correctly fires exactly once per unique `(source_id, sequence_number)`.

The real cause is **cross-Lume arrival-time variance**:

- Badge A (line-of-sight to Director): direct arrival at local time `t_A_arrival`, renders at `t_A_arrival`.
- Badge B (behind an obstruction, needs one relay): direct never lands; via-repeater arrival at `t_B_arrival = t_A_arrival + hop_delay`, renders at `t_B_arrival`.

Both badges render exactly once (dedup is correct); they render at *different local instants* because their local arrival times differ. On LR ESP-NOW a single hop adds ~5-15 ms; a 3-hop cascade adds 15-45 ms. That crosses the human sync-perception threshold (~30-50 ms).

## 2. Root cause

Nothing on the wire tells a Lume when the Director *sent* the frame. Every Lume renders at its own `millis()` at receipt. Without a shared reference, they cannot converge.

The infrastructure to convert local time ↔ director time is already in Phase 1: `HEARTBEAT.tick` (u32 LE ms) is broadcast every 1 s, and Lumes maintain a smoothed `director_tick_offset_ms` per TOFU-locked Director. What's missing is a **per-frame timestamp** on the payloads that matter for sync (`LIGHT_PULSE`, `LIGHT_WASH_PULSE`).

## 3. Proposed change

Add a `send_tick` field (u32 LE ms, matches `HEARTBEAT.tick` semantics) to `LightPulsePayload` and `LightWashPulsePayload`. Director stamps it at emit-time (`send_tick = now_ms()`). Every receiver, direct and via-relay, sees the same `send_tick` in the same logical frame — no matter how many hops it took.

Lumes render at director-time `send_tick + kFleetRenderDelayMs`, converted to local time via the tracked offset:

```
local_fire_ms = send_tick + kFleetRenderDelayMs - director_tick_offset_ms
```

All badges converge on the same local moment regardless of arrival path.

### Which payloads get `send_tick`

| Payload | `send_tick`? | Reason |
|---|---|---|
| `LightPulsePayload` | **Yes** | Beat-cadence sparkles need fleet sync. |
| `LightWashPulsePayload` | **Yes** | Sparkle overlay on a wash; same sync sensitivity as `LightPulse`. |
| `LightWashPayload` | **Yes** (resolved 2026-07-22) | The `attack` fade-in normally hides 10-30 ms desync, but cue authors will sometimes want `attack = 0` for instant colour switches. Airtime cost is 4 bytes per wash — negligible. Get them all in sync. |
| `LightWashEndPayload` | No | Wash cancel + release fade; the release fade masks any desync at wash-end. |
| `HeartbeatPayload` | Already has `tick` | No change. |
| `TextDisplayPayload`, `BitmapHeaderPayload`, `BitmapPlanePayload`, `ClearScreenPayload` | No | Text / image content; ~100 ms latency imperceptible. |
| `RepeaterHeartbeatPayload` | No | Repeater census; latency-insensitive. |

## 4. Wire delta

### Protocol version

```diff
- constexpr uint8_t kProtocolVersion = 0x02;  // bumped from 0x01 for the magic-prefix wire change
+ constexpr uint8_t kProtocolVersion = 0x03;  // bumped from 0x02 for send_tick on pulse payloads
```

Receivers with `protocol_version < 0x03` reject the frame at the header validation gate (existing behaviour per protocol manual §2.1). A mixed-version fleet drops v0x03 frames on old firmware — deploy fleet-wide.

### `LightPulsePayload` (9 → 13 bytes)

```diff
struct LightPulsePayload {
    uint8_t target_class;
    uint8_t target_group;
    uint8_t r, g, b;
    uint8_t attack;
    uint8_t sustain;
    uint8_t release;
    uint8_t chance;
+   uint32_t send_tick;   // director's now_ms() at emit; little-endian
};
- constexpr uint8_t kLightPulsePayloadLen = 9;
+ constexpr uint8_t kLightPulsePayloadLen = 13;
```

### `LightWashPulsePayload` (9 → 13 bytes)

```diff
struct LightWashPulsePayload {
    uint8_t target_class;
    uint8_t target_group;
    uint8_t r, g, b;
    uint8_t attack;
    uint8_t sustain;
    uint8_t release;
    uint8_t chance;
+   uint32_t send_tick;   // director's now_ms() at emit; little-endian
};
- constexpr uint8_t kLightWashPulsePayloadLen = 9;
+ constexpr uint8_t kLightWashPulsePayloadLen = 13;
```

### `LightWashPayload` (16 → 20 bytes)

```diff
struct LightWashPayload {
    uint8_t  target_class;
    uint8_t  target_group;
    uint8_t  r1, g1, b1;
    uint8_t  r2, g2, b2;
    uint8_t  attack;
    uint8_t  release;
    uint8_t  intensity;
    uint16_t cycle_ms;
    uint16_t ttl_seconds;
    uint8_t  pulse_response;
+   uint32_t send_tick;   // director's now_ms() at emit; little-endian
};
- constexpr uint8_t kLightWashPayloadLen = 16;
+ constexpr uint8_t kLightWashPayloadLen = 20;
```

Wash sends fire only on cue change (not every frame), so the airtime cost is trivial. `attack = 0` cues (instant colour switch) now sync with the pulse cadence.

### Airtime cost

4 bytes × 8 bits ÷ 500 kbps (LR bitrate) = **64 μs per pulse**. At 8 Hz peak sparkle rate that's 512 μs/s — 0.1 % of the pipe. Wash sends are cue-change-only, negligible additional. Total v0x03 airtime overhead vs. v0x02 is well under 1 % of the pipe.

## 5. Encoder side (Director)

**StickC** ([`espnow_broadcast_driver.cpp`](../../StickC/src/dal/drivers/espnow_broadcast_driver.cpp)):

```cpp
bool EspNowBroadcastDriver::send(uint8_t target_class,
                                  uint8_t target_group,
                                  const RgbPulseEvent& ev) {
    ...
    LightPulsePayload p{};
    ...
    p.chance     = static_cast<uint8_t>(ev.chance);
+   p.send_tick  = now_ms();   // v0x03: stamp emit-time for cross-Lume sync
    ...
}
```

Same one-line addition in `send_wash_pulse`. `frame.cpp` encoder/decoder gains a `uint32_le` (de)serialize for the new field. Trivial.

**Tildagon** ([`render_dispatch.py`](../../Tildagon/Nocturnation-Tildagon/nocturnation/director/render_dispatch.py)):

```python
def dispatch(self, target, ev, now_ms):
    ...
    payload = encode_light_pulse(
        source_id=self._source_id,
        sequence=self._sequence,
        ...
+       send_tick=now_ms,      # v0x03
    )
```

Same pattern in `dispatch_wash_pulse`. `nocturnation/protocol/frame.py` gains a `struct.pack("<I", send_tick)` field on encode and matching u32 LE unpack on decode.

## 6. Decoder side (Lume)

**StickC** ([`lume_mode.cpp:on_recv`](../../StickC/src/modes/lume_mode.cpp)):

Current LIGHT_PULSE dispatch fires bindings immediately at receipt time (`fan_out_light_pulse_inline` for callback-safe drivers; `pending_light_payload_` deferred for blocking drivers). v0x03 replaces the immediate stamp with a director-time fire schedule:

```cpp
if (hdr.message_type == MessageType::LightPulse
    && m.len == kHeaderSize + kLightPulsePayloadLen) {
    LightPulsePayload p{};
    if (decode_light_pulse(hdr, m.data + kHeaderSize,
                             m.len - kHeaderSize, p) == DecodeResult::Ok) {
        // v0x03 sync scheduling. Convert director-time send_tick to
        // our local fire time. Fall back to immediate render if the
        // Lume hasn't yet locked a HEARTBEAT tick offset (e.g. first
        // second after boot) - old behaviour is the safe default.
        const uint32_t local_fire_ms = director_offset_valid_
            ? (p.send_tick + kFleetRenderDelayMs - director_tick_offset_ms_)
            : millis();
        pending_pulses_.push({local_fire_ms, p});   // ring-buffer queue
        // Late arrivals: if local_fire_ms is already past, dispatch
        // immediately next loop_tick instead of holding for a phantom
        // future time (would happen with a stale hop past kFleetRenderDelayMs).
    }
}
```

`loop_tick` drains the pending queue front-to-back for any entry where `fire_at_ms <= millis()`, calling `fan_out_light_pulse_inline` + setting `pending_light_` as today.

Same pattern for `LightWashPulse`.

**Tildagon** ([`app.py:_observe_frame`](../../Tildagon/Nocturnation-Tildagon/app.py)):

Same shape in Python — a `self._pending_pulses` list of `(local_fire_ms, frame)` tuples, drained on each `background_task` iteration.

## 7. `kFleetRenderDelayMs` sizing

Must cover the worst expected hop-path delay so no relayed frame arrives past its fire time. Retransmit jitter only matters when the first copy is lost (retransmits are spaced 5-15 ms apart per spec §4.3). ESP-NOW LR reliability is high with 2× redundancy, so first-copy-lost is rare — typical arrival ≈ radio propagation time.

| Deployment | Max hops | Typical arrival | Worst-case arrival (first-copy lost) |
|---|---|---|---|
| Direct-only | 0 | 1-3 ms | ~15 ms |
| 1 engineered repeater | 1 | 5-15 ms | ~25 ms |
| 2 engineered repeaters (cascaded) | 2 | 10-30 ms | ~45 ms |
| 3-hop mesh (audience `repeat=AUTO`) | 3 | 15-45 ms | ~60 ms |

**Proposed: `kFleetRenderDelayMs = 30` (resolved 2026-07-22 — DnB context).**

Rationale for tightness: Null Sector-class drum & bass sets hit 180-200+ BPM (some DJs run tracks even hotter). At 180 BPM one beat is 333 ms; at 200 BPM 300 ms; rapid half-time rolls at 200 BPM are 150 ms apart. Every additional millisecond of render delay eats into perceptibility on those short intervals. 30 ms is:

- **10 % of a 200 BPM beat** — imperceptible in the mix
- **20 % of a 150 ms rapid roll** — still just about below the visual sync-perception floor
- Comfortable margin for **direct + 1-hop engineered deployments** (the typical StickC-repeater case)
- Tight but functional for **2-hop cascades** — most frames arrive under budget
- **3-hop cascade at the top end will regularly hit the fallback path** (arrival past deadline → immediate render on that badge). Acceptable — 3-hop audience-mesh already trades latency for coverage, and `repeat=OFF` is the fresh-install default from v1.0.1 so this is opt-in.

**Escape valves:**
- **Build-time override**: `-DNOCT_FLEET_RENDER_DELAY_MS=N` per firmware env. Deep-mesh deployments can raise to 60 ms; direct-only bench setups can drop to 15 ms.
- **Per-cue override** (§11 Q3, resolved: yes): cue-file directive `@fleet_delay <ms>` overrides for that cue only. Rare but supported — one specific cue can loosen the window without affecting the rest of the show.

**Fallback still preserves rendering:** a frame arriving past `send_tick + kFleetRenderDelayMs - director_tick_offset_ms` fires *immediately* on the next `loop_tick` rather than being dropped. So a 3-hop badge in a mostly-2-hop fleet renders slightly late on that one badge, not silently missed.

## 8. Fallback semantics

Two cases where a Lume falls back to *immediate* render (pre-v0x03 behaviour):

1. **Fresh boot, no HEARTBEAT yet.** `director_offset_valid == false`. `director_tick_offset_ms` unknown; we don't know how to convert send_tick to local. Render immediately.
2. **Frame arrived past its fire time.** Deep-mesh or slow-processing path pushed arrival past `send_tick + kFleetRenderDelay`. Render immediately next `loop_tick`.

Both preserve "pulse still fires, just not in sync" over "pulse silently drops."

Fallback logging on both fleets: `[nocturnation] pulse fired past sync deadline; hops=N delay=Nms` at a rate-limited cadence (every 8th occurrence) so operators can bench-see when the deployment is out of budget.

## 9. Migration

- Fleet-wide firmware update required. Old (v0x02) receivers drop v0x03 frames at header validation (per protocol manual §2.1). Old (v0x02) Director frames won't have `send_tick`; new (v0x03) receivers... also drop them at version validation. Bidirectional break; deploy in lock-step.
- Docs update: `manuals/protocol-manual.md` §2.4 (payload table) and §4.3 (LIGHT_PULSE / LIGHT_WASH_PULSE) get the `send_tick` field and worked example.
- No NVS key change; no config-menu change.

## 10. Related tightenings (out of scope for this doc; propose after v0x03 lands)

- **Repeater retransmit jitter reduction.** Current spec §4.3 says 5-15 ms of pseudo-random jitter between retransmits. Relayed frames could use 0-2 ms — every hop saves ~10 ms. Trade-off: slightly higher collision risk under interference.
- **Skip redundancy on relay.** Director emits 3× (LR), repeaters currently also emit 3×. Cut repeater relay to 1× — the origin redundancy already delivered reliability. Trade-off: relay-path frame loss from ~2 %³ ≈ negligible to ~2 % per hop.

Both would live in `RepeaterMode` on the StickC and the dynamic-repeater FSM (`repeater.py`) on the Tildagon.

## 11. Open questions — resolved 2026-07-22

1. **~~Include `send_tick` on `LightWashPayload` too?~~** **Yes.** Extra 4 bytes per wash-cue-change is negligible; `attack = 0` cases benefit. See §3, §4.
2. **~~Sub-second delta instead of full u32?~~** **Full u32.** Wire-cost savings (~2 bytes per pulse) don't justify the decode complexity. C++ decode is free but MicroPython gets extra bytecode per pulse for the wrap-safe delta reconstruction — for the marginal wire savings not worth the perf hit on a 133 MHz Xtensa core running interpreted Python. Match the `HEARTBEAT.tick` semantic exactly (u32 LE ms).
3. **~~Should `kFleetRenderDelayMs` be per-cue?~~** **Yes.** Cue-file directive `@fleet_delay <ms>` planned (see §7 escape valves). Rare use but zero cost when unused; supports one-off cues that want tighter or looser windows without a firmware rebuild.
4. **~~Backwards-compatible mode?~~** **No.** Full fleet-wide upgrade. Clean wire, simpler encoder/decoder, no legacy branching. Documented as a hard version step in §9.

## 12. Roll-out sequence (once approved)

1. **Merge PRs #34 + #23** (Phase 1: HB unconditional + offset tracking) — prerequisite.
2. **`frame.h` + `frame.cpp` (StickC)**: bump `kProtocolVersion`, extend `LightPulsePayload` + `LightWashPulsePayload`, update encoders/decoders + tests.
3. **`frame.py` (Tildagon)**: same for the Python encoder/decoder + tests.
4. **Director-side stamping** (StickC + Tildagon): one-line `send_tick = now_ms()` in each pulse dispatch site.
5. **Lume-side scheduling** (StickC `lume_mode.cpp` + Tildagon `app.py`): pending-pulse queue + drain on tick. Fallback logic.
6. **Docs update**: `protocol-manual.md` §2.4 + §4.3.
7. **Bench verification**: 4-badge fleet, 2 direct + 2 via one repeater, sparkle_on_beat cue. Serial-log each fire timestamp; confirm cross-Lume variance drops from ~15 ms to < 2 ms.

Each of steps 2-6 is a separate PR per fleet, reviewable independently. Full sequence should take 3-4 focused sessions.
