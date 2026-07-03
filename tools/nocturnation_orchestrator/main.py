"""Orchestrator main loop.

Wires the now-playing backend, cue file matcher, FX engine, and
output dispatcher into one ~50 Hz tick loop.

Loop body each tick:
    1. Maybe poll the now-playing backend (once per POLL_INTERVAL_MS).
       - On track change: load the matching cue file, switch
         scheduler, run any file default_fx.
       - On no-source / no-cue-match / parse-failure: stop the
         scheduler AND start Blackout so the fleet actively fades
         to zero (a bare stop just cancels FX, leaving the last
         wash on the wire).
    2. Advance the scheduler against the interpolated position.
    3. Tick the FX engine, writing the universe.
    4. Send the universe via the output dispatcher.
    5. Sleep until the next tick boundary.

Designed to run as a long-lived process; Ctrl+C cleanly drops the
runner, dispatcher, and any open serial port.
"""

import time as _time

from nocturnation_dmx import espnow_frame

from .cues import Cue, parse_cues_file
from .fx import library  # noqa: F401  side-effects: register all FX
from .fx.library.blackout import Blackout
from .fx.registry import fx_registry
from .fx.runner import FxRunner
from .matcher import find_cue_path, slugify
from .scheduler import CueScheduler, PositionTracker


TICK_INTERVAL_MS = 20    # 50 Hz
# nowplaying-cli is ~50 ms per call. 250 ms keeps re-anchor jitter
# under ~250 ms (vs ~1 s at the original 1 Hz) for ~20% CPU on the
# subprocess. Audio output latency dominates everything below this
# (wired ~30 ms, Bluetooth 100-250 ms with environmental variance)
# so polling faster than ~4 Hz buys nothing the operator can hear.
POLL_INTERVAL_MS = 250
POLL_LOG_QUIET_MS = 10_000  # debug poll-log min cadence when state is steady


def _now_ms():
    return int(_time.monotonic() * 1000)


def _stop_to_black(scheduler, runner, now_ms):
    """Tear down the scheduler AND hand off to the Blackout FX, so a
    zero universe reaches the wire before the runner goes idle.

    A bare ``scheduler.stop()`` only cancels whatever FX is running;
    per fx/library/blackout.py's docstring, a bare cancel leaves the
    fleet glowing on the last wash it was given (the StickC mapper
    holds channels, the Lume holds LIGHT_WASH with TTL=0=infinite).
    Used on every no-source / no-cue-match / parse-failure teardown
    path so those look the same to the fleet as an explicit `stop`
    cue mid-song.
    """
    scheduler.stop(now_ms=now_ms)
    runner.start(Blackout.id, now_ms=now_ms)


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

    # Epic 13 display-content state. Tracked here (not in the scheduler
    # or the dispatcher) because HeaderText: / BodyText: cues are
    # incremental - each cue updates ONE field, and we compose the
    # current (header, body) into every TEXT_DISPLAY frame so the Lume
    # always sees the full state. sequence wraps 1..255 (0 = dedup
    # disabled per protocol).
    display_state = {"header": "", "body": "", "sequence": 1}

    def _next_display_sequence():
        s = display_state["sequence"]
        display_state["sequence"] = (s % 255) + 1
        return s

    def emit_text_display(header="", body="", rgb=(255, 255, 255),
                          ttl_ms=0, target_group=0):
        display_state["header"] = header
        display_state["body"] = body
        try:
            frame = espnow_frame.encode_text_display(
                sequence=_next_display_sequence(),
                target_group=target_group,
                r=rgb[0], g=rgb[1], b=rgb[2],
                ttl_ms=ttl_ms,
                header=header,
                body=body,
            )
        except ValueError as exc:
            log("display: encode failed: %s" % exc)
            return
        ok = dispatcher.send_espnow_frame(frame)
        if debug:
            log("display: TEXT_DISPLAY %s header=%r body=%r len=%d"
                % ("sent" if ok else "REJECTED",
                   header, body, len(frame)))
        elif not ok:
            # Production mode: still surface dispatch failures so a
            # missing display path doesn't fail silently.
            log("display: dispatcher rejected TEXT_DISPLAY emission")

    def emit_clear_screen(target_group=0,
                          clear_text=True, clear_bitmap=True):
        display_state["header"] = ""
        display_state["body"] = ""
        try:
            frame = espnow_frame.encode_clear_screen(
                sequence=_next_display_sequence(),
                target_group=target_group,
                clear_text=clear_text,
                clear_bitmap=clear_bitmap,
            )
        except ValueError as exc:
            log("display: encode failed: %s" % exc)
            return
        ok = dispatcher.send_espnow_frame(frame)
        if debug:
            log("display: CLEAR_SCREEN %s text=%d bitmap=%d"
                % ("sent" if ok else "REJECTED",
                   int(clear_text), int(clear_bitmap)))
        elif not ok:
            log("display: dispatcher rejected CLEAR_SCREEN emission")

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

    def on_display_cue(cue, position_ms):
        if cue.kind == "header_text":
            if debug:
                log("[%s] head:  %r" % (_fmt_pos(position_ms), cue.text))
            emit_text_display(header=cue.text, body=display_state["body"])
        elif cue.kind == "body_text":
            if debug:
                log("[%s] body:  %r" % (_fmt_pos(position_ms), cue.text))
            emit_text_display(header=display_state["header"], body=cue.text)
        elif cue.kind == "clearscreen":
            if debug:
                log("[%s] clear: screen" % _fmt_pos(position_ms))
            emit_clear_screen()

    scheduler = CueScheduler(
        runner,
        on_cue_fire=on_cue_fire,
        on_lyric=on_lyric,
        on_bpm_change=on_bpm_change,
        on_display_cue=on_display_cue,
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
                        _stop_to_black(scheduler, runner, now_ms=now)
                        current_track_key = (None, None)
                        # Epic 13: blank the Lume screens when the
                        # source goes away. Otherwise the last
                        # song's text lingers indefinitely.
                        if display_state["header"] or display_state["body"]:
                            emit_clear_screen()
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
                        # Epic 13: on every track change, clear the
                        # Lume screens. The new track's @ShowSongInfo
                        # / first display cue can immediately repaint;
                        # the clear ensures the previous track's text
                        # doesn't linger on a no-match transition.
                        if display_state["header"] or display_state["body"]:
                            emit_clear_screen()
                        path = find_cue_path(
                            songs_dir, snapshot.artist, snapshot.title,
                            genre=snapshot.genre,
                        )
                        if path is None:
                            log("matcher: no cue file for %s [genre=%s]; "
                                "going silent"
                                % (slug, genre_label))
                            _stop_to_black(scheduler, runner, now_ms=now)
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
                                _stop_to_black(scheduler, runner, now_ms=now)
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
                                # Epic 13: synthesize @ShowSongInfo as
                                # HeaderText/BodyText cues at time_ms=
                                # kShowSongInfoAtMs (1 second into the
                                # song) BEFORE handing the cue file to
                                # the scheduler. Three benefits:
                                #   1. Re-fires automatically on a
                                #      backward seek (song restart -
                                #      cursor resets to 0 and the
                                #      synthesised cues re-fire at 1s).
                                #   2. Composes naturally with the
                                #      scheduler's seek-collapse: a
                                #      mid-song join past 1s collapses
                                #      the synthesised cues with any
                                #      later HeaderText/BodyText cues
                                #      into a single final-state
                                #      emission, no flicker.
                                #   3. 1-second offset (vs time_ms=0)
                                #      dodges the burst of activity at
                                #      song start - stop FX cancels
                                #      previous wash, mapper emits
                                #      LIGHT_WASH_END, wash_with_sparkle
                                #      starts and the next frame
                                #      typically lands at ~0:00.5. The
                                #      radio TX queue is congested in
                                #      that ~500 ms window; the title
                                #      card was getting dropped. By
                                #      ~1.0 s the FX has settled and
                                #      the queue is quiet.
                                # Snapshot fields are authoritative
                                # (they reflect what the music player
                                # is actually playing); fall back to
                                # the cue file's @title / @artist if
                                # the snapshot is empty.
                                kShowSongInfoAtMs = 1000
                                if cue_file.show_song_info:
                                    # Artist as header (the larger
                                    # font tier on Lume LCDs), title
                                    # as body. Operator preference -
                                    # billing-style now-playing card.
                                    hdr = snapshot.artist or cue_file.artist or ""
                                    bod = snapshot.title or cue_file.title or ""
                                    if debug:
                                        log(("song-info: synth at %dms header=%r body=%r "
                                             "(snapshot title=%r artist=%r; "
                                             "file title=%r artist=%r)")
                                            % (kShowSongInfoAtMs, hdr, bod,
                                               snapshot.title, snapshot.artist,
                                               cue_file.title, cue_file.artist))
                                    synth = []
                                    if hdr:
                                        synth.append(Cue(
                                            time_ms=kShowSongInfoAtMs, kind="header_text",
                                            text=hdr, line_no=0,
                                        ))
                                    if bod:
                                        synth.append(Cue(
                                            time_ms=kShowSongInfoAtMs, kind="body_text",
                                            text=bod, line_no=0,
                                        ))
                                    if synth:
                                        # Prepend + re-sort. Stable sort
                                        # keeps the synth pair ahead of
                                        # any explicit display cue at
                                        # time_ms=0 in the same priority
                                        # bucket, so an author who writes
                                        # `00:00 HeaderText: Custom` can
                                        # override the synth card.
                                        cue_file.cues = synth + cue_file.cues
                                        _KIND_ORDER = {"bpm": 0, "fx": 1}
                                        cue_file.cues.sort(
                                            key=lambda c: (
                                                c.time_ms,
                                                _KIND_ORDER.get(c.kind, 9),
                                            )
                                        )
                                elif debug:
                                    log("song-info: skipped (@ShowSongInfo not set)")

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
            # current_position correctly returns the FROZEN position
            # when music is paused, so we compute it unconditionally
            # and pass it to runner.tick below - that's what lets
            # beat-aware FX freeze with the music instead of pulsing
            # off wall-clock (bench-found bug: badge kept pulsing
            # while music was paused).
            current_position_ms = (
                tracker.current_position(now)
                if scheduler.cue_file is not None
                else None
            )
            if tracker.is_playing and scheduler.cue_file is not None:
                scheduler.advance(current_position_ms, now_ms=now)

            # 3) Tick the FX engine. Capture pre-tick state too so a
            # one-tick FX (notably Blackout - the `stop` cue) still
            # gets its zero universe dispatched even though it
            # finishes mid-tick.
            pre_tick_active = runner.is_active
            runner.tick(now, universe, position_ms=current_position_ms)

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
