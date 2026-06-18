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

- **PixMob bracelets** (driven through `PixMobIrBinding` over IR): fire-and-forget ASR pulse hardware that does NOT auto-render any persistent state. Each IR command produces one envelope; the bracelet returns to dark after release. **Originally classified as pulse-only**, but bench testing in Epic 11 (2026-06-18) confirmed the bracelet honours long-sustain `SingleColor` envelopes (e.g. `T_3840_MS` = ~3.84 s held colour) and a periodic refresh at 3000 ms cadence produces a **continuous wash with no visible gaps**. So the bracelet *can* support the wash family — not via the protocol's documented `SetColor(background)` slot (that stores RAM state but doesn't auto-render), but via Director-side periodic refresh built into `PixMobIrBinding`. See §10 below for the encoding decisions. Cancellation works cleanly: stopping the refresh lets the last envelope complete naturally, providing a clean "terminate FX early" mechanism with no carry-over.
- **Tildagon badges** (perimeter LED ring + round LCD): can hold state, can run a continuous wash, can overlay pulses on washes. The MicroPython app drives both surfaces and can keep a baseline rendered indefinitely.
- **M5 Stick screens** (via `LocalDisplayBinding`, treated as a Screen-class light surface per §7.4): same capability set as Tildagon — hold state, run a wash, overlay pulses.
- **Future native NocturNation wearables** (post-Epic-7): assume the full capability set unless their binding declares otherwise.

The point of this Epic is to make those capability differences a first-class property of the binding, so the dispatch layer can route protocol traffic correctly without the Director needing to know how each Lume is built. The architectural principle: **the wire protocol carries semantic events; the binding decides hardware encoding per Lume class**.

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

| Binding                | `can_pulse` | `can_wash` | `can_overlay` | Notes |
|------------------------|:---:|:---:|:---:|---|
| `PixMobIrBinding`      | ✓ | ✓ (Epic 11) | ✓ (Epic 11) | Wash via Director-side periodic `SingleColor` refresh; overlay via `TwoColors`. See §10. |
| `LocalDisplayBinding`  | ✓ | ✓ | ✓ | LCD wash phase machine — native hold-state. |
| Tildagon ring + screen | ✓ | ✓ | ✓ | Receives `LIGHT_WASH` over ESP-NOW and runs its own renderer. |

When in doubt about a new binding, declare `{true, false, false}` — the safer baseline. The struct is extensible: future capability flags slot in without breaking existing bindings (they default-init to `false` and are silently dropped by capable-only message paths).

**Pre-Epic-11 status**: `PixMobIrBinding` was declared `{can_pulse: true, can_wash: false, can_overlay: false}`. Epic 11 (bench-validated 2026-06-18) flipped it to full wash-capable based on the periodic-refresh mechanism described in §10. Historical commits with the pulse-only declaration should be read with that context — it wasn't wrong at the time; we hadn't yet discovered the refresh trick.

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

## 10. PixMob wash via Director-side periodic refresh (Epic 11)

Added 2026-06-18 after Epic 11's B-0.5 bench experiments empirically established the encoding decisions for PixMob bracelets. This section is normative for `PixMobIrBinding`.

The wire protocol carries semantic events (`LIGHT_WASH`, `LIGHT_WASH_PULSE`, `LIGHT_WASH_END`). The binding receives them, holds per-`target_group` wash state, and emits IR commands at the cadence the bracelet hardware needs. The orchestrator and the Show framework should NEVER make PixMob-specific encoding decisions — that's the binding's job, by the architectural principle in §1.

### Bench-grounded design parameters

All parameters below come directly from the Epic 11 B-0.5 bench results:

| Parameter | Value | Why |
|---|---|---|
| Refresh cadence | **3000 ms** | T5 confirmed 3000 ms produces no visible gaps with the documented `T_3840_MS` sustain. Comfortable headroom; faster also works but doesn't visibly differ. |
| Refresh envelope | **`T_0_MS / T_3840_MS / T_0_MS`** (square wave) | Snap on (always — regardless of the wash's configured attack, which is used only for the initial `LIGHT_WASH` first-fire and not for subsequent refreshes; see §10b for why), hold, snap off. T5 with `T_480_MS` release showed a visible decay tail between refreshes; square envelope eliminates it. Snap-on attack also keeps the bracelet's deaf-window short, so sparkles fired during an active wash land reliably (see §10b). |
| Cancel behaviour | **Stop refreshing** | The bracelet's currently-active envelope completes naturally — snap-off at sustain end since release is `T_0_MS`. No special cancel command needed. |
| Faded cancel | **One `SingleColor(rgb, T_0, T_0, release_bucket)`** | When `LIGHT_WASH_END.release_time > 0`, after stopping refresh, fire one final envelope with a release tail sized to the bucket closest to `release_time × 100 ms`. |

### IR encoding per wash-family event

| Event | IR encoding | Notes |
|---|---|---|
| `LIGHT_WASH` (any) | `SingleColor(r1,g1,b1, T_0, T_3840, T_0)` + start periodic refresh at 3000 ms cadence | If `cycle_ms > 0`, the binding computes the live blended A↔B colour at each refresh based on cycle phase |
| `LIGHT_WASH_PULSE` (and `LIGHT_PULSE` on a washing group) | Two back-to-back `SingleColor` commands: (a) `SingleColor(sparkle_rgb, orchestrator envelope)` — the visible flash; (b) `SingleColor(current_wash_rgb, T_192_MS, T_3840_MS, T_0_MS)` — the fast recovery to wash colour. See §10a "Why TwoColors isn't used" below. |
| `LIGHT_WASH_END` (instant) | Stop refreshing | Last envelope completes naturally |
| `LIGHT_WASH_END` (faded) | Stop refreshing + one `SingleColor(current_rgb, T_0, T_0, release_bucket)` | Bracelet fades to black over the release time |

### What the SetColor(background) path is NOT used for

Bench experiments (Epic 11 T1/T2/T3) confirmed that `SetColor(isBackground=true)` stores a colour in RAM but **does not auto-render it as a tint**. The bracelet flashes the colour briefly then goes dark. So:

- `LIGHT_WASH` is NOT encoded as `SetColor(background)` — that was the original draft, falsified by bench.
- `LIGHT_WASH_END` is NOT encoded as `SetColor(0,0,0,background)` — that briefly re-renders the *previously*-stored colour before going dark.

The `SetColor`/`CycleProfiles` family stays in `pixmob_protocol.h` for parity completeness and any future use-case where storing state without rendering is genuinely useful (e.g. fleet provisioning), but is not used by the wash family.

### "Terminate FX early" mechanism

Because the cancel path is *"stop refreshing"* and the bracelet snap-offs naturally, the binding gains a useful side-property: **any in-flight wash can be cleanly terminated mid-cycle** without leaving the bracelet stuck on a stale colour. The dispatch layer's `LIGHT_WASH_END` handler (or a `LIGHT_WASH` to the same target with new anchors) cleanly halts the current refresh stream — bracelets converge to the new state within one sustain window (≤3.84 s in the worst case, immediately on the next refresh in the typical case). This is the early-termination guarantee the Show framework can rely on.

### Per-group multiplexing

The binding holds **one wash state per `target_group`** (10 slots, 0..9). Each refresh fires its own `SingleColor` with the matching `restrictGroupId`; bracelets stored in group N respond only to their group's refresh. Worst-case IR airtime if all 10 groups wash simultaneously: ~17% of the IR channel (10 groups × 1 command / 3000 ms × 50 ms per command).

### §10a. Why `TwoColors` isn't used for sparkle-on-wash

The `TwoColors` protocol command (type `0b010`) is documented in `jamesw343`'s reverse-engineering notes as "flash colour 1 briefly (~25 ms), then hold colour 2 with a default 384 ms sustain" — which is a *perfect* primitive for sparkle-on-wash: one command, one IR transmission, the bracelet handles the kick-then-tail itself. The original Epic 11 design (and the implementation in commit `f1b4e39`) used exactly this.

**Bench finding 2026-06-18 (PMob Bench T6):** `buildTwoColors(red, blue)` fired directly via the IR driver, bypassing all wash state, **produces no visible output** on the Aurora-class bracelets Epic 11 targets. Neither the red flash nor the blue tail renders. The bracelet appears to not recognise type `0b010` at all in this firmware revision. The previously-shipping TwoColors-based code was therefore a silent no-op: the wash held smoothly but the sparkles never landed visibly.

The working composition uses **two back-to-back `SingleColor` commands**:

1. **The sparkle.** `SingleColor(sparkle_rgb, ev.attack, ev.sustain, ev.release, chance, group)` — uses the orchestrator's envelope verbatim. The bracelet snaps to the sparkle colour and begins that envelope.
2. **The recovery.** `SingleColor(current_wash_rgb, T_192_MS, T_3840_MS, T_0_MS, CHANCE_100, group)` — fires immediately after the sparkle. The bracelet pre-empts the sparkle envelope and morphs over ~192 ms from wherever it currently is to the wash colour. `current_wash_rgb` is the drift sample at the moment of the pulse (no lookahead — the periodic refresh now uses `T_0_MS` attack so there's no morph-time to compensate for; see §10b).

Visible effect: the sparkle is the **inter-command IR gap** (~50 ms) — the bracelet's snap-to-sparkle phase before the recovery command lands. At 120 BPM (500 ms between beats) the bracelet spends ~250 ms on wash colour between sparkles, which reads as "wash with accents" rather than "strobe with dark gaps".

**Trade-offs accepted:**

- 50 ms sparkle is at the lower bound of human flash perception. Sparkles read as flicker, not as discrete flashes — that's the cost of doing this on hardware that doesn't honour TwoColors.
- IR airtime per sparkle is **2 commands** (~100 ms wire time) instead of 1 (~50 ms). At a sustained 4 Hz sparkle rate, that's ~40% IR utilisation. Acceptable but a real upper bound on simultaneous-group capacity.
- The 192 ms recovery attack is **decoupled from the wash's configured attack**. The wash's attack is for the initial fade-in (which can be slow, e.g. 2.4 s for a moody intro); the recovery wants to be fast to keep wash continuity. Hardcoding 192 ms in the binding rather than threading another field through the wire feels right — it's an encoding decision, not a Show-layer decision.

**Re-enablement path.** If a future bracelet firmware revision is found to honour TwoColors, the binding can revert to the single-command path; the `pixmob::buildTwoColors` encoder stays in `pixmob_protocol.h` for that day. PMob Bench T6 is the bench test for it — if T6 produces a visible red-flash-then-blue-hold on a future bracelet, the single-command path becomes a viable alternative for that hardware.

### §10b. Why the refresh uses `T_0_MS` attack (snap, not morph)

The original §10 design used the wash's configured attack (e.g. `T_2400_MS` for the test wash's 2.4 s fade-in) on every periodic refresh, so the drift between snapshot A and snapshot B at each refresh boundary would morph smoothly rather than snap. A lookahead-shifted phase sample on the drift blender (`compute_drift_rgb`) compensated for the morph-time so the bracelet's morph-end matched the Tildagon's continuous-drift position at that moment.

**Bench cascade 2026-06-18:**

| Configuration | Sparkle visibility on a one-press bench test |
|---|---|
| Single sparkle, recovery, no gap | ~40 % |
| 3× sparkle burst | ~40 % + duplicate flashes when receptive |
| Sparkle only, no recovery | ~40 % |
| 50 ms gap between sparkle and recovery | 0 % (gap landed on the bracelet decoder's frame-end boundary) |

The 40 % is structural: the bracelet's IR receiver is **deaf while it's painting an attack envelope**. With `T_2400_MS` attack on a 3 s refresh cycle, the bracelet is busy rendering attack for 80 % of every cycle — sparkles arriving in that window get dropped (and the dropouts correlate: all-three of a 3× burst miss when the window is unfavourable, all-three land when it's favourable). Hence 3× burst couldn't help.

**Fix:** the periodic refresh uses `T_0_MS` attack always. The bracelet snaps to the snapshot colour at refresh time and immediately enters its (steady-state, IR-receptive) sustain. The bracelet's deaf-window per refresh cycle shrinks from ~2.4 s to ~0 s, raising the bracelet's overall responsiveness to incoming sparkles.

**Cost:** the drift between A and B reads as **step-wise between snapshots** instead of a continuous morph. To minimise the visible stepping, the refresh cadence auto-scales with `cycle_ms`: the binding aims for ~20 snapshots per A↔B↔A round-trip, clamped to `[250 ms, 3000 ms]`. For a 5 s drift cycle that's a refresh every 250 ms = 20 visibly-distinct snapshot colours per cycle, each adjacent pair only ~5 % of the colour delta apart — close to the eye's threshold for noticing a discrete colour change. Static washes (`cycle_ms == 0`) keep the slow 3000 ms cadence since there's no drift to smooth.

| Drift cycle | Refresh interval | Snapshots per cycle |
|---|---|---|
| 1 s (fast) | 250 ms (floor) | 4 |
| 5 s | 250 ms | 20 |
| 10 s | 500 ms | 20 |
| 30 s | 1500 ms | 20 |
| 60 s (slow) | 3000 ms (cap) | 20 |
| static (0) | 3000 ms | n/a |

IR airtime check at the EMF-2026 deployment ceiling (4 simultaneous groups — broadcast + 3): a 5 s cycle across all 4 groups = 4 × 4 refreshes/s = 16 commands/s × ~50 ms wire each = ~80 % utilisation. Plus 120 BPM sparkles (2 commands × 2 Hz × 50 ms = ~200 ms/s) = ~90 % peak. Tight but workable — typical shows are unlikely to drift all 4 groups simultaneously at max sparkle cadence; if a real deployment configuration pushes past 100 %, sparkle dropouts will be the visible symptom and the floor or snapshots-per-cycle should be tuned down.

**Design philosophy.** PixMob bracelets are essentially legacy IR-remote-controlled lights — bench observation from Jason 2026-06-18: *"not much better than IR remote control. They're really dumb."* The future of crowd lighting on this stack is ESP-NOW Lumes (Tildagon and successor designs) where wash drift renders natively as continuous on the receiver. PixMob is best-effort EMF-2026 deployment, not the long-term aesthetic baseline. Optimising for "sparkles land reliably during a wash" is the right design choice over "drift between A and B is perfectly continuous" given the hardware constraint.

**Re-enablement of smooth drift.** If a future bench finding shows the bracelet IS receptive during attack-render (e.g. on a different bracelet generation, or via a firmware revision), the change is a one-line revert: `fire_wash_refresh` can use `state.attack_100ms` again. The `quantize_100ms_to_pixmob_time` helper stays in place for that day.

### Where this is NOT implemented

The wash logic lives in the **Director-side `PixMobIrBinding`**, not in the laptop-side orchestrator. The orchestrator emits wash semantic events (`LIGHT_WASH` wire frames via the DMX channel surface); the Director's StickC decides per-Lume-class how to encode them. Any laptop-side mechanism that tries to write PixMob-specific commands (e.g. the orchestrator-side `pixmob_refresh.py` stopgap that existed pre-Epic-11) is **architecturally wrong** and must be removed. Wash implementation belongs to the Director who controls the fleet.

---

## Open questions for future Epics

These are deliberately *not* resolved by Epic 6C. They are noted here so a future Epic doesn't have to rediscover them.

- **PixMob baseline simulation via periodic low-intensity PULSE.** §1.2 of the architecture spec (post-Phase H) calls for capable Lumes to use `LIGHT_WASH` and PixMob-class Lumes to "simulate baseline via periodic low-intensity PULSE re-broadcasts every few seconds." Epic 6C documents the design but does **not** implement it as live Director behaviour — it's a Show-or-Director-layer feature that needs its own Show-design pass.
- **Director-side wash composition (multiple stacked washes).** Today, a new `LIGHT_WASH` for the same `target_class`/`target_group` supersedes the previous one. There is no notion of "background wash + foreground wash" on a single Lume. Whether LDs want compositional washes (e.g., a slow ambient bed + a per-section gesture wash) is a real design question — but the current model is "one wash per target," and it's tractable. Compositional washes can layer in later behind a capability flag or a new message type without breaking existing semantics.
- **`cycle_ms` synchronisation across a fleet.** Each Lume runs its own cosine phase keyed to its own `wash_start_ms`. A fleet broadcast in the same frame stays in unison up to ESP-NOW jitter (~5 ms) plus per-device clock skew. For most rooms this is invisible; for a synchronised whole-room cue (e.g., the entire room peaking blue at the same instant) it may not be tight enough. If that becomes a real LD requirement, a future protocol field could carry an absolute *broadcast time* and let Lumes phase-align to it. Not Epic 6C's problem.
- ~~**Tildagon-as-Director sending WASH.** Epic 6B (Tildagon Director mode) ships as pulse-only. Adding wash-sending to the Tildagon Director is a Show-design decision — the Tildagon's IMU-tap is naturally a pulse driver, so wash sending wants a different UI affordance (a held button? a configuration toggle?). Deferred to a future Tildagon-Director Epic.~~ **Closed by Epic 6D B1** (2026-06-01): `ctx.render_wash` / `render_wash_end` / `render_wash_pulse` landed on the Tildagon Director surface; the Conductor reference Show (B3) drives the wash via a manually-stepped `section` enum in Settings + palette cycle on Cycle / CyclePrev, deliberately avoiding a wash-on-tap mapping per §1.2 implications for Director Mode on Tildagon.
- **Wash on the perimeter LED ring's per-LED indexing.** The Tildagon has 12 LEDs around its rim; a uniform wash treats them as a single surface. Future Shows may want per-LED gradient washes (e.g., a hue that drifts around the ring rather than across time). That would need either a new message type or an extension to `LIGHT_WASH`; neither is in scope for 6C.
- **Capability flags beyond `can_pulse`/`can_wash`/`can_overlay`.** The struct anticipates `can_strobe`, `can_text`, `can_animate_screen` and friends. Their semantics are not defined here — they land when the first message type that needs them does.

---

## Existing filter audit (Phase B)

Audited the Lume-side dispatch of inbound `LIGHT_PULSE` (renamed from `LIGHT_COMMAND` in Phase C). Reference: `LumeMode::fan_out_light_pulse` in `src/modes/lume_mode.cpp` (formerly `fan_out_light_command`) — the single function that walks the active-bindings array and decides which bindings receive the call for each incoming frame.

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

**Single-Stick demo caveat — by design, not a gap.** ESP-NOW broadcasts don't echo to the broadcaster, so a Stick acting as both Director and the only Lume in range won't see its own wash on its own LCD. This is the right behaviour, not a gap to close: a StickC's LCD is the **UI surface** in Director / Test / Config modes (menus, status, calibration prompts) and the **lighting surface** only in Lume mode (where `LocalDisplayBinding` is activated and paints full-bleed). A Director-side wash loopback that hijacked the Director's own LCD for the wash would obliterate the UI on the one device the operator is reading. Hardware verification therefore wants **two devices**: one Director (Stick or Tildagon) broadcasting, one Lume (Stick or Tildagon) receiving. The PULSE loopback via `DAL::dispatch_output_class_group` does paint the Director's screen via `LocalDriver` today and is a brief per-beat flash on the UI — tolerable but worth gating on `current_mode == Lume` if it ever becomes intrusive.

**Hardware verification checklist** (run separately on real Sticks):
- [ ] Two Sticks on the same ESP-NOW channel; Lume Stick is wash-capable (LocalDisplayBinding active).
- [ ] Director runs `Wash Demo` (or fires `Test Mode > Wash Test > Fire`).
- [ ] Lume's LCD fades into the orange end of the palette over ~2 s (attack), then drifts smoothly between orange and purple on a ~5 s breath.
- [ ] On a kick from the music, a brighter overlay flashes briefly without snapping the baseline to black.
- [ ] On Show exit (or `Test Mode > Wash Test > Cancel`), the LCD fades to black over ~1 s.

---

## Phase H close-out (2026-05-31)

Epic 6C is complete. The protocol surface (LIGHT_PULSE rename + LIGHT_WASH family), the dispatch layer (BindingCapabilities + capability-gated fan-out), and the renderer (cosine drift + ADR-from-current crossfade) have all landed. The architecture spec (`architecture.md`) absorbs the canonical surface at v0.32:

- **§4.3** now lists five active message types (HEARTBEAT, LIGHT_PULSE, LIGHT_WASH, LIGHT_WASH_END, LIGHT_WASH_PULSE) with full byte layouts. "Why two messages, not one" is rewritten as "Why five messages, not two" with per-type rationale.
- **§7.6** documents the `BindingCapabilities` struct and per-binding declarations. The dispatch capability filter is described as the load-bearing piece that keeps PixMobIrBinding pulse-only by construction.
- **§1.2** gains an "Implementation differs by Lume capability" subsection — capable Lumes use `LIGHT_WASH`; pulse-only Lumes get the §1.2 aesthetic via Show-driven low-cadence PULSE re-broadcasts (Show-design choice, not protocol feature).

This document remains as the deeper design rationale: the state machine, the cosine formula, the overlay semantics, the supersede behaviour, the open questions deferred to future Epics, and the implementation audits (Phase B filter, Phase F renderer). The architecture spec is the canonical *what*; this doc is the canonical *why*.

### Outstanding follow-ups (recorded but deferred)

- **PixMob baseline simulation** via Show-driven low-cadence PULSE re-broadcasts. Documented as a Show-design pattern; not implemented as live Director behaviour. The first Show that wants the §1.2 wash-baseline aesthetic on a PixMob-only fleet will build it.
- **Notion canonical sync** — Jason reconciles `architecture.md` v0.32 back into Notion (357bd06774058...). The local working copy is canonical for now.

### Decisions recorded as closed-NO

- **Director-side wash loopback (rejected 2026-05-31).** Initially proposed during bench testing as a way to let a single Stick visually verify its own wash. Rejected on the LCD-role-per-mode rule: the Stick's LCD is UI in non-Lume modes; hijacking it for a wash would obliterate the menus and status text the operator is reading. The architectural intent is that wash visualisation requires a Lume — a second Stick in Lume mode or any Tildagon Lume — and that constraint is preserved. The Wash Test entry moved from `Config > Utilities` to `Test Mode > Wash Test` in the same pass (`Utilities` is for developer bench tools; the §8.5 operator-facing test catalogue lives in `TestMode`).

---

*Document closes at Epic 6C close. Future Epics may amend; the §1-§9 design contract is stable.*
