"""librosa MIR wrapper for Epic 14 B2 cue file enrichment.

Takes an audio file path; returns a dict matching the sidecar JSON
shape documented in `Docs/manuals/cue-file-schema.md`. No cue file
logic - just audio in, structured analysis out. The CLI wrapper
(`scripts/audio_enrich_cues.py`) joins this output to a cue file via
`cue_rewrite.py`.

librosa is imported lazily inside `analyse()` so importing this
module is cheap (test code that doesn't call `analyse()` doesn't
need librosa installed).

The shape choices below balance:
- "what's a useful section count": roughly 1 section per ~20 s of
  audio, clamped to 4-15. The Epic 14 schema accommodates ~10-15
  sections per typical pop song; clamping prevents weirdness on
  very short tracks or very long mixes.
- "what's a useful number of beats": all of them. librosa returns
  ~600 for a 3-min song at 178 BPM; the file lives in the sidecar
  JSON (gitignored), so size isn't a concern.
"""

from __future__ import annotations

import datetime
import math


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyse(audio_path):
    """Run librosa on an audio file. Returns the analysis dict.

    Imports librosa lazily so this module can be imported (and parts
    of it tested) without librosa installed.

    Args:
        audio_path (str | Path): path to a librosa-readable audio
            file (mp3 / wav / flac / m4a / ogg).

    Returns:
        dict matching the sidecar JSON shape in
        `Docs/manuals/cue-file-schema.md`:

            {
              "tempo":         float (BPM),
              "time_sig":      int (typically 4; librosa doesn't
                                  detect explicitly so we assume 4),
              "key":           str ("C", "C#", ..., "B"),
              "mode":          "major" | "minor",
              "duration_s":    float (seconds),
              "beats":         [float, ...] (seconds),
              "onsets":        [float, ...] (seconds),
              "sections":      [{"start": float, "end": float,
                                  "tempo": float,
                                  "loudness_db": float}, ...],
              "chroma_summary": [float * 12],
              "analysis_tool":  "librosa",
              "librosa_version": str,
              "synced":         ISO-8601 UTC string,
            }
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio_path), sr=None)

    duration_s = float(librosa.get_duration(y=y, sr=sr))

    tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    # librosa returns tempo as a numpy array in recent versions;
    # collapse to a Python float.
    tempo = float(np.asarray(tempo_raw).flat[0])
    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
    onsets = librosa.frames_to_time(onset_frames, sr=sr).tolist()

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1).tolist()
    key, mode = _estimate_key_mode(chroma_mean)

    n_sections = _choose_section_count(duration_s)
    sections = _segment_sections(y, sr, n_sections, tempo, beats)

    return {
        "tempo":          tempo,
        "time_sig":       4,
        "key":            key,
        "mode":           mode,
        "duration_s":     duration_s,
        "beats":          [float(t) for t in beats],
        "onsets":         [float(t) for t in onsets],
        "sections":       sections,
        "chroma_summary": [float(c) for c in chroma_mean],
        "analysis_tool":  "librosa",
        "librosa_version": librosa.__version__,
        "synced":         _utc_now_iso(),
    }


# ---------------------------------------------------------------------------
# Section segmentation
# ---------------------------------------------------------------------------


def _choose_section_count(duration_s):
    """Heuristic: ~1 section per 20 s, clamped to 4..15.

    Very short tracks (~30 s intros / interludes) get a small number;
    very long mixes (~10 min club tracks) cap out so the section
    block doesn't drown the cue file.
    """
    target = max(4, int(round(duration_s / 20.0)))
    return min(target, 15)


def _segment_sections(y, sr, n_sections, tempo, beats):
    """Run librosa agglomerative segmentation on chroma+MFCC features.

    Returns a list of dicts with start, end, tempo (constant across
    sections for now - librosa's per-section tempo is wobbly on
    short segments), and loudness_db (RMS-derived).
    """
    import librosa
    import numpy as np

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    # Concatenate chroma (tonal content) + MFCC (timbral content);
    # together these are the canonical features for similarity-based
    # section segmentation.
    features = np.vstack([chroma, mfcc])

    boundaries = librosa.segment.agglomerative(features, k=n_sections)
    # agglomerative returns k boundaries; convert to k+1 boundary
    # times (start of song through end of song) so we get k sections.
    boundary_times = librosa.frames_to_time(boundaries, sr=sr).tolist()
    boundary_times = [0.0] + boundary_times + [float(librosa.get_duration(y=y, sr=sr))]
    # De-dup adjacent identical timestamps + sort defensively.
    boundary_times = sorted(set(round(t, 3) for t in boundary_times))

    # RMS energy across the full track, per frame. We'll average
    # within each section's frame range to get a per-section loudness.
    rms_frames = librosa.feature.rms(y=y)[0]
    hop = 512  # librosa's default hop_length for rms()
    rms_times = librosa.frames_to_time(
        np.arange(len(rms_frames)), sr=sr, hop_length=hop
    )

    sections = []
    for i in range(len(boundary_times) - 1):
        start = boundary_times[i]
        end = boundary_times[i + 1]
        if end - start < 0.5:
            # Skip degenerate sections (rare; happens on very short
            # bridges or boundary-clustering artefacts).
            continue
        loud = _loudness_db_in_range(rms_frames, rms_times, start, end)
        sections.append({
            "start": float(start),
            "end":   float(end),
            "tempo": float(tempo),
            "loudness_db": float(loud),
        })
    return sections


def _loudness_db_in_range(rms_frames, rms_times, start, end):
    """Mean RMS dB across the [start, end] time range.

    Converts RMS energy to dB via 20*log10(rms). Floor at -60 dB to
    keep silence from going to -infinity.
    """
    import numpy as np

    mask = (rms_times >= start) & (rms_times < end)
    if not mask.any():
        return -60.0
    avg_rms = float(rms_frames[mask].mean())
    if avg_rms <= 1e-6:
        return -60.0
    db = 20.0 * math.log10(avg_rms)
    return max(db, -60.0)


# ---------------------------------------------------------------------------
# Key + mode estimation (Krumhansl-Schmuckler profile matching)
# ---------------------------------------------------------------------------
#
# Score the chroma mean against every major and minor key profile;
# pick the best correlation. Profiles are from Krumhansl &
# Schmuckler's "Cognitive Foundations of Musical Pitch" (1990) - the
# canonical perceptual key profiles for Western tonal music.


_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_MAJOR_PROFILE = (
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
    2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
)
_MINOR_PROFILE = (
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
    2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
)


def _estimate_key_mode(chroma_mean):
    """Return (key_name, mode_name) for the strongest matching profile.

    Args:
        chroma_mean: 12-element list of normalised pitch-class energies.

    Returns:
        ("C"|"C#"|..., "major"|"minor"). Works for most tonal music;
        atonal / heavily modulated / very-short tracks may return
        spurious results - bench-listen and override the @key directive
        by hand if so.
    """
    best_score = -1.0
    best_key = "C"
    best_mode = "major"
    for tonic in range(12):
        for mode_name, profile in (("major", _MAJOR_PROFILE),
                                    ("minor", _MINOR_PROFILE)):
            rotated = profile[-tonic:] + profile[:-tonic] if tonic else profile
            score = _correlation(chroma_mean, rotated)
            if score > best_score:
                best_score = score
                best_key = _PITCH_NAMES[tonic]
                best_mode = mode_name
    return best_key, best_mode


def _correlation(a, b):
    """Pearson correlation, defensive on degenerate inputs."""
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((a[i] - mean_a) ** 2 for i in range(n))
    var_b = sum((b[i] - mean_b) ** 2 for i in range(n))
    denom = (var_a * var_b) ** 0.5
    if denom <= 0:
        return 0.0
    return cov / denom


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _utc_now_iso():
    """ISO-8601 UTC timestamp string (suffix 'Z')."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
