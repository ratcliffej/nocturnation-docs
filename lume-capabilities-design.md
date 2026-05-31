---
title: "Lume capabilities + LIGHT_PULSE / LIGHT_WASH split — design"
status: Draft (Phase A output of Epic 6C)
last_updated: 2026-05-31
source_epic: epics/epic-06c-wash-mode.md
source_prompt: epics/epic-06c-wash-mode-source-prompt.md (Notion v0.3, 2026-05-31)
notion_url: ""
sync_direction: local-first-pr-on-completion
---

# Lume capabilities + LIGHT_PULSE / LIGHT_WASH split — design

This document is the contract for Epic 6C. Every implementation phase (B through G) honours the decisions below without re-debating them; Phase H folds the implemented protocol surface back into the architecture spec (§4.3, §7.6, §1.2). Where this document says *should*, it is normative; where it explains *why*, that is rationale.

## 1. Lume class capability surfaces

NocturNation Lumes vary by *what they can physically do with light*. The current cohort:

- **PixMob bracelets** (driven through `PixMobIrBinding` over IR): fire-and-forget ASR pulse only. The hardware has no persistent state between commands beyond the residual envelope of the last pulse; it cannot hold a colour, cannot run a continuous wash, cannot overlay one command on another. This is a property of the bracelet, not a deficiency of the binding — PixMob's design intentionally treats each command as a stateless envelope.
- **Tildagon badges** (perimeter LED ring + round LCD): can hold state, can run a continuous wash, can overlay pulses on washes. The MicroPython app drives both surfaces and can keep a baseline rendered indefinitely.
- **M5 Stick screens** (via `LocalDisplayBinding`, treated as a Screen-class light surface per §7.4): same capability set as Tildagon — hold state, run a wash, overlay pulses.
- **Future native NocturNation wearables** (post-Epic-7): assume the full capability set unless their binding declares otherwise.

The point of this Epic is to make those capability differences a first-class property of the binding, so the dispatch layer can route protocol traffic correctly without the Director needing to know how each Lume is built.

## 2. The `BindingCapabilities` struct

Every `OutputBinding` declares its capabilities via a single struct returned from a pure-virtual method on the base class:

```cpp
struct BindingCapabilities {
    bool can_pulse;     // Fire an ASR pulse (universal; every binding sets this true).
    bool can_wash;      // Hold a persistent background wash.
    bool can_overlay;   // Render a PULSE additively on top of an active WASH.
    // Future: can_strobe, can_text, can_animate_screen, ...
};

class OutputBinding {
public:
    virtual BindingCapabilities capabilities() const = 0;
    // ...
};
```

Existing-binding declarations:

| Binding                | `can_pulse` | `can_wash` | `can_overlay` |
|------------------------|:---:|:---:|:---:|
| `PixMobIrBinding`      | ✓ | — | — |
| `LocalDisplayBinding`  | ✓ | ✓ | ✓ |
| Tildagon ring + screen | ✓ | ✓ | ✓ |

When in doubt about a new binding, declare `{true, false, false}` — the safer baseline. The struct is extensible: future capability flags slot in without breaking existing bindings (they default-init to `false` and are silently dropped by capable-only message paths).

## 3. `LIGHT_COMMAND` → `LIGHT_PULSE` rename

The existing wire message previously called `LIGHT_COMMAND` is renamed to `LIGHT_PULSE` throughout the codebase, inline comments, log messages, the protocol manual, and tests. **Wire byte 0x03 is unchanged. Payload structure (9 bytes: target_class, target_group, r, g, b, attack, sustain, release, chance) is unchanged.** The rename is purely cosmetic — but it's load-bearing for readability once `LIGHT_WASH` and `LIGHT_WASH_PULSE` sit alongside it. "Pulse" names the *thing* the wire carries (a transient ASR envelope on an instantaneous colour); "command" was overly generic.

The DAL `render_fx(target, ev)` entry point keeps its name — `render_fx` is the *Show-side API*, `LIGHT_PULSE` is the *wire encoding*. The Show framework gains `render_wash` / `render_wash_end` / `render_wash_pulse` as siblings in Phase E.

## 4. New message types

### 4.1 `LIGHT_WASH` (wire byte 0x06, 16-byte payload)

> **Byte-count corrected in Phase D**: the v0.3 source prompt called this a 17-byte payload — that was an arithmetic error in the spec authoring (1+1+3+3+1+1+1+2+2+1 = 16, not 17). The implementation lands as 16 bytes; this doc, the protocol manual, and the Notion source prompt are corrected to match.

| Field | Type | Notes |
|---|---|---|
| `target_class`   | u8       | NocturNation class targeting (see §4.1 carrier in architecture). |
| `target_group`   | u8       | Group within class; 0 = wildcard. |
| `r1`, `g1`, `b1` | u8 each  | **Start colour.** |
| `r2`, `g2`, `b2` | u8 each  | **End colour.** Used only when `cycle_ms ≠ 0`. |
| `attack`         | u8 (100 ms units) | Time to ramp into the wash from the current rendered colour. Range 0–25.5 s. |
| `release`        | u8 (100 ms units) | Default fade-out time when the wash ends (TTL expiry or superseded by another `LIGHT_WASH`). `LIGHT_WASH_END` may override on demand. |
| `intensity`      | u8       | 0–255 brightness scalar applied to the wash baseline (start and end colours both). |
| `cycle_ms`       | u16 LE   | One full A↔B↔A oscillation in milliseconds. **0 = no cycle, hold `r1/g1/b1`** and ignore `r2/g2/b2`. |
| `ttl_seconds`    | u16 LE   | 0 = infinite (held until `LIGHT_WASH_END` or a superseding `LIGHT_WASH`). |
| `pulse_response` | u8       | 0 = ignore PULSE while washing; 1 = accept PULSE as additive overlay. |

**Two-colour drift waveform.** When `cycle_ms ≠ 0`, the renderer interpolates the instantaneous wash colour using a **cosine-eased ping-pong**:

```
t  = 0.5 − 0.5 · cos(2π · (now_ms − wash_start_ms) / cycle_ms)
wash_now = lerp(r1g1b1, r2g2b2, t) · (intensity / 255)
```

Each Lume runs its own phase keyed to its own `wash_start_ms` (the `now_ms()` at the moment the `LIGHT_WASH` was received), so a fleet broadcast in the same frame stays roughly in unison after the attack completes. There is no Director-side sync traffic; the Director sends one frame and walks away. A wash refresh (Phase E) re-stamps `wash_start_ms` and is therefore a *minor* phase reset — acceptable because the cosine ease makes any small jitter invisible.

### 4.2 `LIGHT_WASH_END` (wire byte 0x07, 3-byte payload)

| Field | Type | Notes |
|---|---|---|
| `target_class`  | u8 | Same class targeting as the wash being cancelled. |
| `target_group`  | u8 | Same group. |
| `release_time`  | u8 (100 ms units) | Overrides the active wash's `release` field; the Lume fades from the instantaneous wash colour to black over this duration, then exits wash mode. |

### 4.3 `LIGHT_WASH_PULSE` (wire byte 0x08, 9-byte payload)

Identical payload to `LIGHT_PULSE`: `target_class`, `target_group`, `r`, `g`, `b`, `attack`, `sustain`, `release`, `chance`. The only difference from `LIGHT_PULSE` is the **filter rule** — `LIGHT_WASH_PULSE` fires only on Lumes that are *currently* in wash state. Non-washing Lumes drop the frame.

## 5. Filter rules

The four cases of (wash-state × incoming message), explicit:

| Lume state | Frame received          | pulse_response | Action |
|---|---|---|---|
| Not washing | `LIGHT_PULSE`           | n/a | Fires (existing behaviour). |
| Not washing | `LIGHT_WASH_PULSE`      | n/a | Drops silently. |
| Washing     | `LIGHT_PULSE`           | 0   | Drops silently; wash holds untouched. |
| Washing     | `LIGHT_PULSE`           | 1   | Fires as additive overlay on the instantaneous wash baseline. |
| Washing     | `LIGHT_WASH_PULSE`      | any | Always fires as additive overlay. |

Additionally, **bindings whose capabilities declare `can_wash = false` drop the entire WASH family** (`LIGHT_WASH`, `LIGHT_WASH_END`, `LIGHT_WASH_PULSE`) at the dispatch layer — before the binding's `on_*` handler is called. The Director need not know which Lume classes can wash; capability filtering happens Lume-side.

## 6. Overlay semantics

When a PULSE (or `LIGHT_WASH_PULSE`) renders on top of an active wash, the renderer applies the pulse colour as an **additive blend** against the instantaneous wash baseline:

```
baseline_now    = wash_now (per §4.1 cosine interpolation, already scaled by intensity)
output_at_time  = clip(baseline_now + pulse_colour · pulse_envelope_value, 0, 255)  // per channel
```

`pulse_envelope_value` follows the pulse's own ASR shape (attack ramp, sustain hold, release fade) on a 0–1 scale. On release, the pulse fades back to **whatever the wash baseline is at that moment** — *not* back to black, and *not* back to the start colour. Because the cosine drift keeps moving while the pulse is rendering, this is also why we keep the renderer reading `wash_now` live rather than caching the baseline at pulse-start time.

`intensity` is applied to the wash colour *before* the pulse overlay. The pulse colour is not scaled by `intensity` — a kick is meant to punch through the baseline, regardless of how dim the baseline is.

## 7. TTL semantics

`ttl_seconds = 0` means the wash holds forever — until either a `LIGHT_WASH_END` cancels it or another `LIGHT_WASH` (with the same `target_class`/`target_group`) supersedes it. This is the right default for ambient-baseline work where the Show is the canonical owner of cleanup.

`ttl_seconds ≠ 0` means the Lume runs its own countdown from the wash's receive time. On expiry, the Lume fades from the instantaneous wash colour to black over the wash's `release` field (not overridden — `LIGHT_WASH_END` is for explicit cancellation, not for TTL expiry), then exits wash mode. This is useful for sound-check, debugging, or any case where the Director may stop existing before it can clean up.

The Director **may** re-broadcast a wash periodically (Phase E uses ~10 s) to handle Lume restarts; this is robustness, not protocol. The Lume treats every received `LIGHT_WASH` as fresh — re-stamps `wash_start_ms`, restarts the cosine phase, re-applies attack from current colour. Because the rebroadcast carries the same `r1`/`r2`/`cycle_ms`/`intensity`, the visual result is a near-imperceptible phase nudge; the cosine ease absorbs it.

## 8. Cancellation

`LIGHT_WASH_END` causes the Lume to fade from the instantaneous wash colour to black over the provided `release_time`, then exit wash mode. After cancellation, regular `LIGHT_PULSE` behaviour resumes (fires render against a black baseline). The Lume does *not* preserve any wash state across cancellation — sending another `LIGHT_WASH` later starts fresh.

`LIGHT_WASH_END`'s `release_time` overrides the wash's own `release` field exactly because the two events differ in spirit. The wash's `release` is the default fade for "this wash is ending, naturally or because something else is taking over" (TTL expiry or supersede); `LIGHT_WASH_END`'s `release_time` is a *director's explicit* cancel — the LD can choose a slow elegant fade or a snappy 0.5 s wipe, irrespective of what the original wash declared.

## 9. Wire compatibility

NocturNation is not deployed externally; the project memory ([deployment scope - Mac only](https://memory)) confirms there are no field devices to keep parser-compatible with. So:

- Wire byte 0x03 (`LIGHT_PULSE`, formerly `LIGHT_COMMAND`) keeps its value.
- 0x06, 0x07, 0x08 (`LIGHT_WASH`, `LIGHT_WASH_END`, `LIGHT_WASH_PULSE`) **reuse the previously-deprecated slots** of `MUSIC_EVENT`, `SNARE_DETECTED`, `HIHAT_DETECTED` respectively — all three were removed in v0.27 and no firmware currently parses them.
- No protocol version bump is needed. The wire surface gains new types but does not break existing ones.

Phase D's acceptance criteria includes a grep-check for any leftover parser for `MSG_MUSIC_EVENT` / `MSG_SNARE_DETECTED` / `MSG_HIHAT_DETECTED` before reusing the slots — defensive but cheap.

---

## Open questions for future Epics

These are deliberately *not* resolved by Epic 6C. They are noted here so a future Epic doesn't have to rediscover them.

- **PixMob baseline simulation via periodic low-intensity PULSE.** §1.2 of the architecture spec (post-Phase H) calls for capable Lumes to use `LIGHT_WASH` and PixMob-class Lumes to "simulate baseline via periodic low-intensity PULSE re-broadcasts every few seconds." Epic 6C documents the design but does **not** implement it as live Director behaviour — it's a Show-or-Director-layer feature that needs its own Show-design pass.
- **Director-side wash composition (multiple stacked washes).** Today, a new `LIGHT_WASH` for the same `target_class`/`target_group` supersedes the previous one. There is no notion of "background wash + foreground wash" on a single Lume. Whether LDs want compositional washes (e.g., a slow ambient bed + a per-section gesture wash) is a real design question — but the current model is "one wash per target," and it's tractable. Compositional washes can layer in later behind a capability flag or a new message type without breaking existing semantics.
- **`cycle_ms` synchronisation across a fleet.** Each Lume runs its own cosine phase keyed to its own `wash_start_ms`. A fleet broadcast in the same frame stays in unison up to ESP-NOW jitter (~5 ms) plus per-device clock skew. For most rooms this is invisible; for a synchronised whole-room cue (e.g., the entire room peaking blue at the same instant) it may not be tight enough. If that becomes a real LD requirement, a future protocol field could carry an absolute *broadcast time* and let Lumes phase-align to it. Not Epic 6C's problem.
- **Tildagon-as-Director sending WASH.** Epic 6B (Tildagon Director mode) ships as pulse-only. Adding wash-sending to the Tildagon Director is a Show-design decision — the Tildagon's IMU-tap is naturally a pulse driver, so wash sending wants a different UI affordance (a held button? a configuration toggle?). Deferred to a future Tildagon-Director Epic.
- **Wash on the perimeter LED ring's per-LED indexing.** The Tildagon has 12 LEDs around its rim; a uniform wash treats them as a single surface. Future Shows may want per-LED gradient washes (e.g., a hue that drifts around the ring rather than across time). That would need either a new message type or an extension to `LIGHT_WASH`; neither is in scope for 6C.
- **Capability flags beyond `can_pulse`/`can_wash`/`can_overlay`.** The struct anticipates `can_strobe`, `can_text`, `can_animate_screen` and friends. Their semantics are not defined here — they land when the first message type that needs them does.

---

## Existing filter audit (Phase B)

Audited the Lume-side dispatch of inbound `LIGHT_COMMAND` (the existing wire type that Phase C renames to `LIGHT_PULSE`). Reference: `LumeMode::fan_out_light_command` in `src/modes/lume_mode.cpp` — the single function that walks the active-bindings array and decides which bindings receive the call for each incoming frame.

**What was checked:**

1. **`target_class` filter** is `if (p.target_class != 0 && p.target_class != binding_class) continue;` — drops the frame for any binding whose `device_class()` doesn't match `p.target_class` (with `0` honoured as the All-classes wildcard).
2. **`target_group` filter** is `if (!slot.binding->is_relay()) { if (p.target_group != 0 && p.target_group != lume_group_) continue; }` — for non-relay (local) bindings, drops the frame unless `p.target_group == 0` (broadcast) or matches the Lume's NVS-configured `slv_group`.
3. **Relay-binding bypass** of the group filter — `is_relay() == true` (PixMobIrBinding) skips the group check entirely, because the downstream PixMob IR carries its own group byte for the bracelet to filter against. The relay reads `ctx.current_target_group()` and forwards it as the IR frame's group code.
4. **Context threading** — before invoking the binding's `on_light_command`, the dispatch does `slot.ctx->set_current_target(p.target_class, p.target_group)` so relay bindings can read the inbound addressing without re-plumbing the call signature.
5. **`DeviceClass` enum stability** — `0x00 All / 0x01 Light / 0x02 Screen / 0x03 MultiLedScreen` per `include/hal/device_class.h`; `0x04+` reserved. No conflict with the v0.27-deprecated message-type slots (those are wire bytes; this is the class enum, a separate namespace).

**What was confirmed working:**

- Class filtering is correct: a frame addressed to `target_class = Light (0x01)` reaches only Light-class bindings, regardless of `target_group`. A frame addressed to `target_class = 0` reaches every binding.
- Group filtering is correct for local bindings: a frame with `target_group = 5` reaches a `LocalDisplayBinding` only on a Lume whose `slv_group` is `5` (or `target_group = 0`, the broadcast group, which fires everywhere).
- Relay-binding bypass is correct and necessary: without it, a `target_group = 5` directed at PixMob bracelets would *not* reach `PixMobIrBinding` on a Lume whose `slv_group` is `3` — but PixMob bracelets are addressed by the IR-level group byte, not by the Lume's NVS group, so the relay must forward regardless and let the bracelet filter itself.
- The two filters compose correctly: a class-mismatched frame is dropped before the group check, and the group check is only reached for relay-eligible class-matched frames.

**Gaps to fix:** None at the `target_class` / `target_group` level. The dispatch correctly drops frames a binding can't act on by *addressing*. It does not yet drop frames a binding can't act on by *capability* (e.g. a `LIGHT_WASH` on a `can_wash: false` binding) — but that's Phase F's job, not a bug in the existing filter. The Phase B `capabilities()` declarations land the data; Phase F adds the capability-aware dispatch.

**Scope clarification for Phase F:** The capability filter Phase F will add should sit at the *dispatch* layer (in `fan_out_*`-style functions for the new wire types), not inside the bindings themselves. This keeps the binding implementations free of "is this message meant for me?" boilerplate and matches the existing pattern where the dispatch decides reach.

**No code changed in Phase B beyond the new `capabilities()` overrides** — per the prompt's "fixes go in a later phase to keep blast radius small" instruction. The audit found no fixes to defer.

---

## Lume-side renderer audit (Phase F)

`LocalDisplayBinding` now owns wash rendering end-to-end. The wire receive path (`LumeMode::on_recv` → `fan_out_light_wash[_end|_pulse]`) routes the new wire types to the binding's hooks with a **two-layer filter**: the existing target_class / target_group filter from §5 plus a capability gate that drops the frame on any binding declaring `can_wash = false`. PixMob bindings receive no WASH-family hooks at all, exactly as §1 intends.

**Renderer architecture.** When a wash is **inactive**, `on_light_pulse` still forwards to `DAL::render_fx("local", ev)` exactly as before — `LocalDriver` animates the pulse on the LCD with byte-identical behaviour to pre-Phase-F. When a wash is **active**, the binding takes over the canvas: `tick()` (driven from `LumeMode::loop_tick`, at the main loop's existing cadence) recomputes the instantaneous wash baseline using the design's cosine-eased ping-pong formula and paints the full screen via `DAL::fire_display_clear("local", …)` in RGB565. Pulses that arrive during an active wash short-circuit the render_fx path and instead register an overlay event; the binding additively blends the pulse contribution on top of the live baseline each tick and clips per-channel. The blend formula is the one in §6:

```
out = clip(baseline_now + pulse_colour × envelope_strength, 0, 255)
```

The pulse envelope is a triangular approximation of the ASR shape over `attack+sustain+release` total milliseconds (lookup table for the `pulse::Time` enum). On an LCD at these timescales the triangular approximation is visually indistinguishable from the precise three-segment ramp; this avoids dragging per-segment arithmetic into the render loop.

**Phase state machine.** `Attacking → Holding → (optionally Releasing) → Inactive`. The Attacking phase lerps from the colour the binding was rendering before the wash arrived to the wash baseline over `attack × 100 ms`. Holding runs the cosine drift. TTL expiry (when non-zero) triggers Releasing using the wash's own `release` field. `LIGHT_WASH_END` triggers Releasing with the operator-supplied `release_time` overriding. A second `LIGHT_WASH` to the same target supersedes by re-stamping `wash_started_ms` (cosine phase resets) and re-entering Attacking from the live colour — visually a smooth palette transition.

**Capability dispatch (Phase B/F closure).** The Phase B audit flagged that capability-aware dispatch was a Phase F concern. Confirmed implemented: `LumeMode::fan_out_light_wash[_end|_pulse]` short-circuits the per-binding routing with `if (!slot.binding->capabilities().can_wash) continue;`. Wire frames addressed to PixMob-class targets still pass the class filter (since `PixMobIrBinding::device_class() == Light`), but the capability gate then drops them before `on_light_wash` would be called. PixMobIrBinding therefore needs no changes — the dispatch layer keeps it pulse-only by construction.

**Single-Stick demo caveat.** ESP-NOW broadcasts don't echo to the broadcaster, so a Stick acting as both Director and the only Lume in range won't see its own wash on its own LCD; `DAL::dispatch_output_class_group`'s loopback for PULSE paints the Director's screen via `LocalDriver`, but no equivalent wash-loopback to `LocalDisplayBinding` exists yet. Hardware verification therefore wants **two Sticks** (one Director running `Wash Demo`, one Lume receiving). Adding a wash-loopback step inside `DAL::render_wash` so the Director's own LCD also renders the wash is a small follow-up — recorded in §"Open questions" above; not in Phase F's scope.

**Hardware verification checklist** (run separately on real Sticks):
- [ ] Two Sticks on the same ESP-NOW channel; Lume Stick is wash-capable (LocalDisplayBinding active).
- [ ] Director runs `Wash Demo` (or fires `Config > Utilities > Wash Test > Fire`).
- [ ] Lume's LCD fades into the orange end of the palette over ~2 s (attack), then drifts smoothly between orange and purple on a ~5 s breath.
- [ ] On a kick from the music, a brighter overlay flashes briefly without snapping the baseline to black.
- [ ] On Show exit (or `Wash Test > Cancel`), the LCD fades to black over ~1 s.

---

## Phase H close-out (2026-05-31)

Epic 6C is complete. The protocol surface (LIGHT_PULSE rename + LIGHT_WASH family), the dispatch layer (BindingCapabilities + capability-gated fan-out), and the renderer (cosine drift + ADR-from-current crossfade) have all landed. The architecture spec (`architecture.md`) absorbs the canonical surface at v0.32:

- **§4.3** now lists five active message types (HEARTBEAT, LIGHT_PULSE, LIGHT_WASH, LIGHT_WASH_END, LIGHT_WASH_PULSE) with full byte layouts. "Why two messages, not one" is rewritten as "Why five messages, not two" with per-type rationale.
- **§7.6** documents the `BindingCapabilities` struct and per-binding declarations. The dispatch capability filter is described as the load-bearing piece that keeps PixMobIrBinding pulse-only by construction.
- **§1.2** gains an "Implementation differs by Lume capability" subsection — capable Lumes use `LIGHT_WASH`; pulse-only Lumes get the §1.2 aesthetic via Show-driven low-cadence PULSE re-broadcasts (Show-design choice, not protocol feature).

This document remains as the deeper design rationale: the state machine, the cosine formula, the overlay semantics, the supersede behaviour, the open questions deferred to future Epics, and the implementation audits (Phase B filter, Phase F renderer). The architecture spec is the canonical *what*; this doc is the canonical *why*.

### Outstanding follow-ups (recorded but deferred)

- **Director-side wash loopback** so a single-Stick demo shows its own wash on its own LCD. Today the Phase E `DAL::dispatch_output_class_group` loopback only covers PULSE — wash needs the equivalent. Two-Stick (Director + Lume) demos work fine; the gap is single-Stick visual testing.
- **PixMob baseline simulation** via Show-driven low-cadence PULSE re-broadcasts. Documented as a Show-design pattern; not implemented as live Director behaviour. The first Show that wants the §1.2 wash-baseline aesthetic on a PixMob-only fleet will build it.
- **Notion canonical sync** — Jason reconciles `architecture.md` v0.32 back into Notion (357bd06774058...). The local working copy is canonical for now.

---

*Document closes at Epic 6C close. Future Epics may amend; the §1-§9 design contract is stable.*
