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


def run(
    nowplaying_backend,
    dispatcher,
    songs_dir,
    *,
    default_bpm=120,
    log=print,
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
        sleep, now_ms: injectable for tests.
        universe_size: size of the DMX universe bytearray (default 512).
        iteration_budget: if set, run at most N ticks then return
            (tests / smoke runs).
    """
    runner = FxRunner(fx_registry, default_bpm=default_bpm)
    tracker = PositionTracker()
    scheduler = CueScheduler(runner)
    universe = bytearray(universe_size)

    last_poll_wall_ms = 0
    current_track_key = (None, None)
    iterations = 0

    log("orchestrator: started (songs_dir=%s, default_bpm=%d, output=%s)"
        % (songs_dir, default_bpm, dispatcher.name))

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
