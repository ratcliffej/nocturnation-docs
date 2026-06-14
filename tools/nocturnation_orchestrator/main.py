"""Orchestrator main loop.

Wires the now-playing backend, cue file matcher, FX engine, and
output dispatcher into one ~50 Hz tick loop.

Loop body each tick:
    1. Maybe poll the now-playing backend (once per POLL_INTERVAL_MS).
       - On track change: load the matching cue file, switch
         scheduler, run any file default_fx.
       - On no-source: stop the scheduler (output goes black).
    2. Advance the scheduler against the interpolated position.
    3. Tick the FX engine, writing the universe.
    4. Send the universe via the output dispatcher.
    5. Sleep until the next tick boundary.

Designed to run as a long-lived process; Ctrl+C cleanly drops the
runner, dispatcher, and any open serial port.
"""

import time as _time

from .cues import parse_cues_file
from .fx import library  # noqa: F401  side-effects: register all FX
from .fx.registry import fx_registry
from .fx.runner import FxRunner
from .matcher import find_cue_path, slugify
from .scheduler import CueScheduler, PositionTracker


TICK_INTERVAL_MS = 20    # 50 Hz
POLL_INTERVAL_MS = 1000  # nowplaying-cli is ~50 ms per call - throttle
POLL_LOG_QUIET_MS = 10_000  # debug poll-log min cadence when state is steady


def _now_ms():
    return int(_time.monotonic() * 1000)


def _fmt_pos(position_ms):
    """Render a position-ms value as H:MM:SS.xxx (no leading hours
    when zero)."""
    if position_ms < 0:
        position_ms = 0
    secs, ms = divmod(position_ms, 1000)
    minutes, sec = divmod(secs, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d:%02d:%02d.%03d" % (hours, minutes, sec, ms)
    return "%02d:%02d.%03d" % (minutes, sec, ms)


def _format_cue_for_log(cue):
    cls = fx_registry.get(cue.fx_id)
    name = cls.cue_name if cls is not None else (
        "stop" if cue.fx_id == 0 else "fx_%d" % cue.fx_id
    )
    bits = [name]
    # Show the user-supplied (pre-conversion) values, not the u8 wire
    # form the FX consumes. So a cue file with `probability 30` logs
    # as "30" here, not "76" (the round(30 * 255 / 100) result).
    # Fall back to the u8 form for old code paths that don't populate
    # params_raw (defensive - shouldn't happen for parsed cues).
    raw = cue.params_raw or cue.params
    params = list(raw)
    while params and params[-1] == 0:
        params.pop()
    if params:
        bits.append(" ".join(str(p) for p in params))
    if cue.bpm:
        bits.append("--bpm %d" % cue.bpm)
    if cue.buildup_s:
        bits.append("--buildup %d" % cue.buildup_s)
    return "  ".join(bits)


def run(
    nowplaying_backend,
    dispatcher,
    songs_dir,
    *,
    default_bpm=120,
    log=print,
    debug=False,
    sleep=_time.sleep,
    now_ms=_now_ms,
    universe_size=512,
    iteration_budget=None,
):
    """Run the orchestrator loop until interrupted.

    Args:
        nowplaying_backend: NowPlayingBackend instance.
        dispatcher: OutputDispatcher instance.
        songs_dir: path to the directory of `.cues` files.
        default_bpm: BPM used when neither file nor cue overrides.
        log: one-line status sink. Default print.
        debug: if True, emit per-cue + per-lyric + per-poll log lines
            so you can trace exactly what the orchestrator is picking
            up from the .cues file. Default False (quiet mode).
        sleep, now_ms: injectable for tests.
        universe_size: size of the DMX universe bytearray (default 512).
        iteration_budget: if set, run at most N ticks then return
            (tests / smoke runs).
    """
    runner = FxRunner(fx_registry, default_bpm=default_bpm)
    tracker = PositionTracker()
    last_dispatched = None
    was_active = False

    def on_cue_fire(cue, position_ms):
        if debug:
            log("[%s] cue:   %s" % (_fmt_pos(position_ms),
                                    _format_cue_for_log(cue)))

    def on_lyric(lyric, position_ms):
        if debug:
            log("[%s] lyric: %s" % (_fmt_pos(position_ms), lyric.text))

    def on_bpm_change(cue, position_ms):
        if debug:
            log("[%s] bpm:   %d" % (_fmt_pos(position_ms), cue.bpm))

    scheduler = CueScheduler(
        runner,
        on_cue_fire=on_cue_fire,
        on_lyric=on_lyric,
        on_bpm_change=on_bpm_change,
    )
    universe = bytearray(universe_size)

    last_poll_wall_ms = 0
    current_track_key = (None, None)
    current_cue_path = None
    current_cue_mtime = 0.0
    # Debug-mode poll-log throttle: log every poll on state change,
    # but otherwise no more than once per ~10 s when nothing's
    # happening (same track, same play state). Avoids drowning the
    # log in identical "poll: X (playing=yes)" lines.
    last_poll_logged_ms = -POLL_LOG_QUIET_MS
    last_poll_logged_state = (None, None, None)
    iterations = 0

    log("orchestrator: started (songs_dir=%s, default_bpm=%d, output=%s%s)"
        % (songs_dir, default_bpm, dispatcher.name,
           ", debug=on" if debug else ""))

    try:
        while True:
            now = now_ms()

            # 1) Now-playing poll.
            if now - last_poll_wall_ms >= POLL_INTERVAL_MS:
                last_poll_wall_ms = now
                try:
                    snapshot = nowplaying_backend.poll()
                except Exception as exc:  # pragma: no cover
                    log("nowplaying: %s" % exc)
                    snapshot = None
                if snapshot is None:
                    if current_track_key != (None, None):
                        log("nowplaying: no source")
                        tracker.clear()
                        scheduler.stop(now_ms=now)
                        current_track_key = (None, None)
                else:
                    tracker.update_from_poll(snapshot, now)
                    key = (snapshot.artist, snapshot.title)
                    # Render artist/title using the same slug form
                    # the matcher uses for file lookup, so the LD
                    # reads the log and knows exactly what filename
                    # to author. Falls back to a placeholder when
                    # both fields are empty.
                    slug = slugify(snapshot.artist, snapshot.title) or "(no-track)"
                    genre_label = snapshot.genre or "-"
                    if debug:
                        # Interpolated (live) position is what the
                        # scheduler is using; that's what the LD cares
                        # about. The raw OS position is sticky on
                        # macOS (MediaRemote caches it; nowplaying-cli
                        # `get-raw` reads the same cached value), so
                        # printing it every poll is noise.
                        #
                        # Throttle: log every poll on state change
                        # (track / play-state / first time we see this
                        # snapshot), but otherwise no more than once
                        # per POLL_LOG_QUIET_MS while nothing's
                        # changing. Otherwise --debug fills with
                        # near-identical lines once a song is steady.
                        state = (snapshot.artist, snapshot.title,
                                 snapshot.is_playing)
                        state_changed = state != last_poll_logged_state
                        quiet_elapsed = now - last_poll_logged_ms
                        if state_changed or quiet_elapsed >= POLL_LOG_QUIET_MS:
                            live_pos = tracker.current_position(now)
                            log("[%s] poll:  %s [genre=%s] (playing=%s)" % (
                                _fmt_pos(live_pos),
                                slug,
                                genre_label,
                                "yes" if snapshot.is_playing else "no",
                            ))
                            last_poll_logged_ms = now
                            last_poll_logged_state = state
                    if key != current_track_key:
                        path = find_cue_path(
                            songs_dir, snapshot.artist, snapshot.title,
                            genre=snapshot.genre,
                        )
                        if path is None:
                            log("matcher: no cue file for %s [genre=%s]; "
                                "going silent"
                                % (slug, genre_label))
                            scheduler.stop(now_ms=now)
                            current_cue_path = None
                            current_cue_mtime = 0.0
                        else:
                            log("matcher: %s [genre=%s] -> %s"
                                % (slug, genre_label, path.name))
                            try:
                                cue_file = parse_cues_file(path)
                            except Exception as exc:
                                log("matcher: parse failed for %s: %s"
                                    % (path.name, exc))
                                scheduler.stop(now_ms=now)
                                current_cue_path = None
                                current_cue_mtime = 0.0
                            else:
                                if debug:
                                    offset_note = (
                                        " offset=%+.2fs" % (cue_file.offset_ms / 1000.0)
                                        if cue_file.offset_ms else ""
                                    )
                                    log("loaded: %d cues, %d lyric anchors, "
                                        "default_fx_id=%d, default_bpm=%d%s"
                                        % (len(cue_file.cues),
                                           len(cue_file.lyrics),
                                           cue_file.default_fx_id,
                                           cue_file.default_bpm,
                                           offset_note))
                                scheduler.set_cue_file(cue_file, now_ms=now)
                                current_cue_path = path
                                try:
                                    current_cue_mtime = path.stat().st_mtime
                                except OSError:
                                    current_cue_mtime = 0.0
                        current_track_key = key
                    elif current_cue_path is not None:
                        # Same track. Detect re-save and hot-reload so the
                        # LD can author live while a song plays. Polling
                        # mtime once per now-playing cycle (~1 Hz) is
                        # plenty - the user is editing the file by hand.
                        try:
                            new_mtime = current_cue_path.stat().st_mtime
                        except OSError:
                            new_mtime = 0.0
                        if new_mtime and new_mtime != current_cue_mtime:
                            try:
                                new_file = parse_cues_file(current_cue_path)
                            except Exception as exc:
                                log("reload: parse failed for %s: %s; "
                                    "keeping previous version"
                                    % (current_cue_path.name, exc))
                            else:
                                pos = tracker.current_position(now)
                                scheduler.reload_cue_file(
                                    new_file, now_ms=now, position_ms=pos,
                                )
                                log("reload: %s (%d cues, %d lyric anchors, "
                                    "applied at %s)"
                                    % (current_cue_path.name,
                                       len(new_file.cues),
                                       len(new_file.lyrics),
                                       _fmt_pos(pos)))
                                current_cue_mtime = new_mtime

            # 2) Advance the scheduler against interpolated position.
            if tracker.is_playing and scheduler.cue_file is not None:
                position_ms = tracker.current_position(now)
                scheduler.advance(position_ms, now_ms=now)

            # 3) Tick the FX engine. Capture pre-tick state too so a
            # one-tick FX (notably Blackout - the `stop` cue) still
            # gets its zero universe dispatched even though it
            # finishes mid-tick.
            pre_tick_active = runner.is_active
            runner.tick(now, universe)

            # 4) Dispatch - with two layers of suppression so we look
            # like a polite DMX producer rather than a firehose:
            #
            # (a) Skip when no FX is loaded. If we sent an all-zero
            #     universe at startup, the StickC mapper would seed
            #     itself with wash anchors of (0, 0, 0) and then
            #     debounce away the REAL wash that arrives ~20 ms
            #     later (the mapper's 50 ms wash-emit gap floor),
            #     leaving the Lume frozen on a black wash. Gating on
            #     runner.is_active makes the StickC see IDLE until an
            #     FX actually fires.
            #
            # (b) Skip when the universe is byte-identical to the
            #     last send. A static wash (the common case) then
            #     dispatches exactly ONCE; the StickC LCD naturally
            #     alternates ACTIVE / IDLE the way QLC+ does, USB
            #     bandwidth drops ~50x, and the single-core Plus2
            #     spends less time in UART / parser / mapper paths.
            #     The Lume sees no behavioural difference - the
            #     StickC's change-detection layer was already
            #     emitting one LIGHT_WASH and then going quiet, so
            #     suppressing repeats upstream is purely additive.
            #
            # On the inactive -> active transition we drop the cache
            # so the FIRST frame of the new FX always lands on the
            # wire even if its bytes happen to equal the last frame
            # sent before going quiet.
            # Active EITHER pre-tick (caught a one-tick FX writing
            # values before finishing) OR post-tick (a long-running
            # FX still writing). Either way we want to dispatch.
            if pre_tick_active or runner.is_active:
                if not was_active:
                    last_dispatched = None
                    was_active = True
                universe_bytes = bytes(universe)
                if universe_bytes != last_dispatched:
                    try:
                        dispatcher.send(universe)
                        last_dispatched = universe_bytes
                    except Exception as exc:  # pragma: no cover
                        log("output: send failed: %s" % exc)
            else:
                was_active = False

            # 5) Iteration budget (tests).
            iterations += 1
            if iteration_budget is not None and iterations >= iteration_budget:
                return

            # 6) Sleep to the next tick boundary.
            spent = now_ms() - now
            wait = (TICK_INTERVAL_MS - spent) / 1000.0
            if wait > 0:
                sleep(wait)
    finally:
        log("orchestrator: shutting down")
        try:
            runner.cancel(now_ms=now_ms())
        except Exception:
            pass
        try:
            dispatcher.close()
        except Exception:
            pass
