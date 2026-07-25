---
title: "Fleet render synchronisation — design"
status: Draft
sync_version: 0.1
last_updated: 2026-07-22
supersedes: wire-spec-v0x03-pulse-sync-design.md (rev 4958cdd, git history only)
---

# Fleet render synchronisation — design

Replaces the reverted v0x03 `send_tick` approach with a scheme that:

- Requires **no wire-spec bump** (protocol stays at v0x02).
- Requires **no lockstep flash** — old and new firmware interoperate.
- Requires **no cross-device clock alignment** — no HEARTBEAT tick tracking, no EWMA drift smoothing, no Director-vs-Lume clock domain.
- Falls back gracefully to current "render on receipt" behaviour when disabled.

## 1. Problem

Two failure modes cause visibly-desynchronised pulse rendering across the fleet at DnB tempos (180+ BPM, sixteenth-note interval ~80 ms):

**A. Hop-0-to-hop-0 render-tick phase drift.** Two Tildagons receiving the same broadcast paint their perimeter LEDs 0-50 ms apart because the perimeter tick runs at 20 Hz (50 ms cadence) and the two devices have independent phase. `dispatch()` stamps envelope start-time at frame receipt but the LED write happens on the next tick, so identical broadcasts render at up-to-50-ms-apart wall-clock instants.

**B. Multi-hop cascade delay.** With repeater relay chains, each hop adds ~5-10 ms typical (up to 30-50 ms with GC spikes) between receive and re-broadcast. A hop-3 Lume renders 15-45 ms after a hop-0 Lume for the same original frame.

Failure A is the ground truth — until we tighten hop-0-to-hop-0, no per-hop compensation scheme will land clean sync. Failure B is only worth solving once A is closed.

## 2. Non-goals

- Bumping the wire protocol (`kProtocolVersion` stays at `0x02`).
- Adding fields to `LIGHT_PULSE` / `LIGHT_WASH` / `LIGHT_WASH_PULSE` payloads.
- Cross-device clock alignment or `send_tick`-style anchoring.
- Any lockstep firmware requirement across the fleet.
- Reducing battery endurance below current 60-80 min (crowd-merch fleet must survive whole show).

## 3. Phased roll-out

Three phases, each independently landable and independently valuable. **Phase 1 must land + bench-verify before Phase 2 starts.**

### Phase 1 — hop-0 render tightening (MUST land first)

Close the render-tick phase gap so two Tildagons receiving the same broadcast paint their LEDs within ~2 ms of each other, not up to 50 ms.

### Phase 2 — hop-adaptive delay + HEARTBEAT config channel

Compensate for multi-hop cascade using a per-hop delay table, transported via unused HEARTBEAT payload bits. Fully backward compatible.

### Phase 3 — Redundant-send Director config

Make the Director's 2× redundant transmit an operator-toggleable NVS setting. Reduces airtime + downstream jitter in well-repeatered venues.

---

## 4. Phase 1 — hop-0 render tightening

### 4.1 Bench measurement (blocks implementation)

Before choosing the fix, measure the actual hop-0-to-hop-0 spread between two Tildagons:

- Two Tildagons on the same channel, side-by-side, oriented at ~90° so a photodiode can see both perimeter rings.
- Director sends `LIGHT_PULSE` at 2 Hz with a bright short envelope (T_0 sustain, T_96 attack, T_0 release — snap-on).
- Log per-Tildagon receive `now_ms`, dispatch `now_ms`, and next render-tick `now_ms` to serial.
- Compute per-frame per-Tildagon "paint delay" (render-tick — receive) and inter-Tildagon "paint delta" (A paint — B paint) over ~100 frames.

Expected before fix: paint delta 0-50 ms uniform-random, dominated by render-tick phase. Expected after each candidate fix below: paint delta ≤ 2 ms.

### 4.2 Candidate fixes

Choose after bench measurement confirms the dominant source is render-tick phase (not asyncio poll granularity or WiFi-task ordering).

**Option A — inline paint on dispatch (recommended pending measurement).** In `_observe_frame`, immediately after `self._renderer.dispatch(frame, now_ms)`, call `self._render_perimeter()` inline instead of waiting for the next 20 Hz tick. The background render tick continues to drive envelope decay between pulses at 20 Hz — the inline paint only handles the initial pulse-visible-instant.

Cost: +1 `leds.write()` per admitted LIGHT_PULSE (~1 ms bit-bang for 12 LEDs). Under sustained 8-10 pulses/s during heavy DMX, adds ~10 ms/s CPU. Comfortable.

Benefit: pulse becomes visible within one asyncio poll (5 ms) of frame receipt, independent of render-tick phase. Two Tildagons with 5 ms poll-phase difference show 5 ms visible delta, not 50 ms.

**Option B — high-priority render slot.** Keep the 20 Hz tick but let a fresh LIGHT_PULSE bypass the interval check for one paint. Simpler to reason about than inline paint but same net cost.

**Option C — burst render rate around pulse fires.** After a pulse arrives, run render at 100 Hz for the envelope duration (~200 ms). Higher CPU cost, may nibble battery. Not preferred over A/B.

### 4.3 Success criterion

Two-Tildagon bench: hop-0 paint delta ≤ 2 ms P95, ≤ 5 ms worst-case over 100 frames. Below this, Phase 2 is worth starting.

### 4.4 Same fix applies to `LIGHT_WASH_PULSE`

Wash-pulse dispatch goes through the same render tick. Apply the same inline-paint (or equivalent) fix to `on_light_wash_pulse` code path.

---

## 5. Phase 2 — hop-adaptive delay + HEARTBEAT config channel

### 5.1 Delay table

Base delay `B` in milliseconds (fleet-wide, Director-controlled). Per-frame delay computed at the receiver as:

```
delay_ms = max(0, B - hop_count × slope)
where slope = B / 3
```

With `B = 45`: table is 45/30/15/0 ms for hop 0/1/2/3. Bench-measured per-hop cost sets `B` — aim for `B ≥ 3 × (worst per-hop delay)` so the hop-3 case doesn't compute a negative delay.

Bench numbers pending. Guess: `B = 20` if per-hop is ~5 ms after Phase 1 tightening, `B = 45` if per-hop is ~15 ms.

### 5.2 Config transport — HEARTBEAT payload bit-steal

The 9-byte HEARTBEAT payload is:

```
0-3  tick             u32 LE   (Director millis, load-bearing)
4-5  days_since_2026  u16 LE   (0 today; reserved for Tier 3 wallclock)
6-8  centiseconds     u24 LE   (0 today; reserved for Tier 3 wallclock)
```

Directors currently emit `0` for the last two fields (see [protocol-manual.md §3.3.1](manuals/protocol-manual.md)). We can steal high bits without breaking a future Tier 3 wallclock rollout because the real ranges don't need those bits:

- `days_since_2026` real range: 0-9999 (year 2026 to 2053), fits in 14 bits. Bits 15 and 14 are free.
- `centiseconds` real range: 0-8,639,999 (100 × 86,400), fits in 23 bits. Bit 23 is free.

Repurposed layout:

```
Byte 4-5  days_since_2026 (u16 LE)
  bit 15:      fleet_repeat_enabled  (0/1)
  bit 14:      sync_enabled          (0/1)
  bits 0-13:   days_since_2026 (0-9999 usable, 0-16383 addressable)

Byte 6-8  centiseconds_today (u24 LE)
  bit 23:      reserved              (future flag)
  bits 16-22:  reserved              (future config)
  bits 0-15:   hop_delay_base_ms (0-65535 addressable, 0-255 used)
```

Note: `hop_delay_base_ms` occupies the two LSBs of centiseconds (bits 0-15), so a Director emitting real wallclock centiseconds and sync-config simultaneously would need bits 16-22 for wallclock and reserve bits 0-15 for config. Since Tier 3 isn't wired yet and a real-wallclock Director would emit the current centiseconds directly, this LSB-steal is safe *today* but must be revisited before Tier 3 lands. Alternative: dedicate a full byte 6 to `hop_delay_base_ms` and put centiseconds in bits 0-15 of bytes 7-8 (u16), constraining wallclock to 100 × 655.35 s = 18 hours. That's a whole-day break — not usable for wallclock. **Recommendation: hop_delay_base_ms goes in bits 0-15 of centiseconds, marked as "consumed until Tier 3 revisits".**

Directors that don't emit sync config write these bits as 0 (which is what they do today anyway). Old firmware ignores them (nothing consumes wallclock currently). New firmware masks them out before wallclock decode.

### 5.3 Lume-side logic

On admittable `LIGHT_PULSE` or `LIGHT_WASH_PULSE` receive (after dedup, TOFU, wash-active gate):

```python
if not sync_enabled:
    render_immediately(frame)
else:
    h = frame.hop_count
    d = max(0, hop_delay_base_ms - h * (hop_delay_base_ms // 3))
    fire_at_ms = now_ms + d
    if pending_queue_full():
        render_immediately(frame)  # fallback
    else:
        queue.append((fire_at_ms, frame))
```

The `sync_enabled` and `hop_delay_base_ms` values come from the last-seen HEARTBEAT from the locked Director. If no HEARTBEAT has arrived (fresh TOFU lock, or first frame is `LIGHT_PULSE`), default to `sync_enabled = 0` → immediate render.

Pending queue: 8 entries per pulse family (LIGHT_PULSE / LIGHT_WASH_PULSE separately). Overflow falls back to immediate render — same overflow behaviour as v0x03's queue would have had.

### 5.4 Drain

Hardware-timer-driven single-shot at `fire_at_ms`. On MicroPython Tildagon, `machine.Timer()` with `mode=Timer.ONE_SHOT`. Callback pushes the frame into `_render_perimeter` inline (same path as Phase 1 inline paint).

Drift: ESP32 crystal ±20 ppm × 50 ms = 1 µs. Invisible. No accumulation because each frame is a fresh single-shot.

Fallback: if timer allocation fails (all 4 hardware timers busy — unlikely), fall through to the current asyncio drain at 5 ms poll granularity. Adds ~5 ms jitter, still better than 50 ms.

### 5.5 Director-side emission

Two operator controls in Config menu:

- `fleet_sync = OFF / ON` — sets `sync_enabled` bit in every HEARTBEAT.
- `hop_delay_base_ms = 20 / 30 / 45 / 60` — sets `hop_delay_base_ms` byte in every HEARTBEAT. Preset list rather than free int for menu simplicity.

Both persisted in NVS. Default: `fleet_sync = OFF`, `hop_delay_base_ms = 45`.

`fleet_repeat_enabled` bit similarly: Director UI toggle "Repeat=OFF / ON" broadcasts the fleet-wide preference. Lumes with `Repeat=AUTO` (Tildagon setting) honour this; Lumes with `Repeat=OFF` or `Repeat=ON` locally override.

---

## 6. Phase 3 — redundant-send Director config

Current StickC behaviour: every emitted frame is TX'd twice with random 5-15 ms jitter between copies ([espnow_broadcast_driver.h:64-66](StickC/src/dal/drivers/espnow_broadcast_driver.h#L64-L66)). Reasons: (a) recover from correlated interference on the second copy, (b) give receiver-side callback queues time to drain between arrivals.

Both reasons dissolve when the venue is well-repeatered: the mesh gives multiple paths to every Lume via different hop chains, and a Lume that misses the first arrival gets a second chance from a different physical neighbour a few ms later anyway.

### 6.1 Config

Director NVS: `redundant_sends ∈ {1, 2}`. Default `2` (current behaviour). Operator sets `1` when repeaters cover the venue.

### 6.2 Implementation

Gate the second-send scheduling in `send_frame_bytes` on `redundant_sends >= 2`. Persist via `Preferences` (StickC uses this everywhere).

No wire change.

### 6.3 Interaction with Phase 2

`redundant_sends = 1` combined with `sync_enabled = 1` gives the tightest sync in a repeater mesh: single Director TX (no jitter) → mesh distributes → each Lume compensates its per-hop delay. This is the operator-target for stage EMF-style deployment.

`redundant_sends = 2` combined with `sync_enabled = 0` is the current pre-EMF long-range default: retry-heavy transmit, receive-on-arrival render. Deployed Lumes see no change from either optimisation.

---

## 7. Backward compatibility

Every device runs on wire spec `v0x02` before and after. Payload byte layouts unchanged for `LIGHT_PULSE`, `LIGHT_WASH`, `LIGHT_WASH_PULSE`, `LIGHT_WASH_END`, and text/bitmap types. HEARTBEAT payload bytes remain 9 with `payload_len = 9`.

| Fleet state | Director behaviour | Lume behaviour |
|---|---|---|
| Old firmware everywhere | Emits `days = 0`, `centiseconds = 0`. | Ignores wallclock bits. Renders on receipt. |
| New Director + old Lumes | Emits sync-config bits. | Old Lumes read wallclock fields as 0-adjacent, ignore. Render on receipt. |
| Old Director + new Lumes | Emits `days = 0`, `centiseconds = 0`. | New Lumes see `sync_enabled = 0` bit → render on receipt (current behaviour). |
| New everywhere, `sync_enabled = 0` | Emits config bits (all zero). | Renders on receipt. Zero perf change. |
| New everywhere, `sync_enabled = 1` | Emits config with `hop_delay_base_ms`. | Deferred render at `now + delay(hop)`. |

No lockstep flash. No dropped frames. Zero migration cost per Lume.

---

## 8. Roll-out sequence

1. **Bench-measure hop-0 desync between two Tildagons.** Instrument, log, decide Phase 1 fix. Success gate: measurement reveals dominant source is render-tick phase (or something else — data may surprise us).
2. **Land Phase 1 (hop-0 render tightening).** Ship inline paint (or equivalent). Verify with same two-Tildagon bench: paint delta ≤ 2 ms P95.
3. **Bench-measure per-hop cascade delay** — one Director + two Tildagons chained via repeater. Sets `hop_delay_base_ms` for Phase 2.
4. **Land Phase 2 (Lume-side hop-adaptive delay + HEARTBEAT config decode).** `sync_enabled` never set from Director yet; verify zero regression against Phase 1.
5. **Land Phase 2 Director side** — Config menu toggles, HEARTBEAT bit emission. Verify multi-Tildagon cascade with `sync_enabled = 1` renders in sync.
6. **Land Phase 3 (redundant-send Director config).** Verify airtime reduction on-air with a spectrum analyser or channel-utilisation log.

Each phase lands as its own PR against `main` on both firmware repos; no long-lived branches. Docs updates land alongside the code they document.

---

## 9. Open questions

**Q1: Actual hop-0 spread today.** Blocked on bench measurement (§4.1). Guess: 0-50 ms uniform, but the shape (uniform vs bimodal from asyncio poll phase interaction) affects the fix.

**Q2: Wallclock bit-steal safety.** The proposed `centiseconds` bit-0-15 steal for `hop_delay_base_ms` clashes with a future real-wallclock Director. Alternatives:

- **A** (recommended): steal bit 23 of centiseconds only as a "config present" flag, plus bits 15-14 of `days_since_2026` for `sync_enabled` and `fleet_repeat_enabled`. `hop_delay_base_ms` lives in a NEW extension message type (`0xFE` or similar) sent every N heartbeats, not in HEARTBEAT itself. Clean separation.
- **B** (as designed above): steal centiseconds low bits for `hop_delay_base_ms`. Simple, but blocks Tier 3 wallclock.
- **C**: don't bit-steal at all; use the reserved `EXTENSION` frame type (`0xFF`) with a defined config sub-format. New message on the wire but backward compat holds (§3.2 rule: unknown types silently dropped).

Recommend deciding between A and C before Phase 2 code lands. Both preserve Tier 3 upside.

**Q3: Delay table shape.** Slope-derived (single `hop_delay_base_ms` byte) or per-hop table (4 bytes)? Slope is simpler and easier to config-menu; per-hop is more expressive if per-hop delay turns out to be non-uniform (e.g. hop-1 costs 8 ms, hop-2 costs 12 ms). Bench data (§step 3) resolves this.

**Q4: Behaviour when HEARTBEAT is lost.** If sync_enabled was 1 but HEARTBEAT stops arriving (long DMX-only traffic pattern, e.g. 4 seconds of sustained pulses with the unconditional-1Hz HEARTBEAT filtered by a bug), do we keep applying the delay or fall back to immediate? Recommend: fall back to immediate after 3 s of no HEARTBEAT — same threshold as NO SIGNAL — so a signal-loss state doesn't stack sync-defer on top of no-config-refresh.

**Q5: Hardware-timer allocation.** Tildagon uses `machine.Timer(-1)` (virtual soft timer) or one of 4 hardware timers. Confirm which is available without conflicting with other subsystems (audio, IR, screen refresh). If none free, fall back to asyncio drain with the 5 ms granularity penalty.

---

## 10. Reference

- Reverted v0x03 attempt: `wire-spec-v0x03-pulse-sync-design.md` at git rev `4958cdd` (removed from `main` 2026-07-22). Key idea (`send_tick` field) is not part of this design; kept in history for future comparison.
- Cross-Lume desync memory: `v0x03_attempt_rolled_back.md` in Claude memory.
- Perimeter renderer: [nocturnation-tildagon/nocturnation/render/perimeter.py:284](Tildagon/Nocturnation-Tildagon/nocturnation/render/perimeter.py#L284) `dispatch()`.
- Perimeter tick site: [nocturnation-tildagon/app.py:1056](Tildagon/Nocturnation-Tildagon/app.py#L1056) `_render_perimeter()` invoked from receive-loop at 20 Hz.
- StickC redundant-send constants: [nocturnation-stickc/src/dal/drivers/espnow_broadcast_driver.h:64-66](StickC/src/dal/drivers/espnow_broadcast_driver.h#L64-L66) `kRedundantGapMinMs = 5`, `kRedundantGapMaxMs = 15`.
- HEARTBEAT wire layout: [manuals/protocol-manual.md §3.3.1](manuals/protocol-manual.md).
