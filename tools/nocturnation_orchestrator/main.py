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
from .matcher import find_cue_path
from .scheduler import CueScheduler, PositionTracker


TICK_INTERVAL_MS = 20    # 50 Hz
POLL_INTERVAL_MS = 1000  # nowplaying-cli is ~50 ms per call - throttle


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
    # Only show non-default param slots: trim trailing zeros so a cue
    # with one positional param doesn't print 5 trailing zeros.
    params = list(cue.params)
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

    def on_cue_fire(cue, position_ms):
        if debug:
            log("[%s] cue:   %s" % (_fmt_pos(position_ms),
                                    _format_cue_for_log(cue)))

    def on_lyric(lyric, position_ms):
        if debug:
            log("[%s] lyric: %s" % (_fmt_pos(position_ms), lyric.text))

    scheduler = CueScheduler(runner, on_cue_fire=on_cue_fire, on_lyric=on_lyric)
    universe = bytearray(universe_size)

    last_poll_wall_ms = 0
    current_track_key = (None, None)
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
                    if debug:
                        # Show interpolated position (what the
                        # scheduler is actually using) as the headline
                        # value, plus the raw OS-reported position in
                        # parentheses so stale-cache behaviour is
                        # diagnosable from one log.
                        live_pos = tracker.current_position(now)
                        log("[%s] poll:  %s / %s (playing=%s, OS pos=%s)" % (
                            _fmt_pos(live_pos),
                            snapshot.artist, snapshot.title,
                            "yes" if snapshot.is_playing else "no",
                            _fmt_pos(snapshot.position_ms),
                        ))
                    if key != current_track_key:
                        path = find_cue_path(
                            songs_dir, snapshot.artist, snapshot.title,
                        )
                        if path is None:
                            log("matcher: no cue file for %s / %s; going silent"
                                % (snapshot.artist, snapshot.title))
                            scheduler.stop(now_ms=now)
                        else:
                            log("matcher: %s -> %s" % (key, path.name))
                            try:
                                cue_file = parse_cues_file(path)
                            except Exception as exc:
                                log("matcher: parse failed for %s: %s"
                                    % (path.name, exc))
                                scheduler.stop(now_ms=now)
                            else:
                                if debug:
                                    log("loaded: %d cues, %d lyric anchors, "
                                        "default_fx_id=%d, default_bpm=%d"
                                        % (len(cue_file.cues),
                                           len(cue_file.lyrics),
                                           cue_file.default_fx_id,
                                           cue_file.default_bpm))
                                scheduler.set_cue_file(cue_file, now_ms=now)
                        current_track_key = key

            # 2) Advance the scheduler against interpolated position.
            if tracker.is_playing and scheduler.cue_file is not None:
                position_ms = tracker.current_position(now)
                scheduler.advance(position_ms, now_ms=now)

            # 3) Tick the FX engine.
            runner.tick(now, universe)

            # 4) Dispatch.
            try:
                dispatcher.send(universe)
            except Exception as exc:  # pragma: no cover
                log("output: send failed: %s" % exc)

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
