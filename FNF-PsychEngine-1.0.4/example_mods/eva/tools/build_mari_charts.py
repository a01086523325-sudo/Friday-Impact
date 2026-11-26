#!/usr/bin/env python3
"""Build three Mari songs for this Psych Engine mod from the supplied MP3s.

Requirements: Python 3, numpy, scipy, librosa >= 0.10, ffmpeg with libvorbis.
    python3 -m pip install --user 'librosa>=0.10.2,<0.12' 'scipy>=1.11,<2'
    python3 /home/ubuntu/work/eva/tools/build_mari_charts.py

Inputs default to tools/mari_source/mari_track{1,2,3}.mp3. Use --audio-dir
and --output-root to test with other paths. Existing target files are NEVER
overwritten without --force; unrelated files, including Voices.ogg, are untouched.
Conversion and all chart generation/validation occur in a temporary directory
before any output is installed. No vocals stem is present (needsVoices=false).

Notes are placed at measured spectral-flux onsets, not a free-running BPM grid.
librosa's beat tracker provides a musical pulse/phase for filtering onsets and
section timing. Global and locally adaptive tempo tracks are compared against
onset alignment; if neither is reliable, note selection falls back to onsets.
Psych section BPMs describe consecutive four-beat intervals; note timestamps
remain absolute milliseconds, so small tempo or phase errors do not move notes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

try:
    import numpy as np
    import librosa
    from scipy.ndimage import median_filter
    from scipy.spatial import cKDTree
except ImportError as exc:
    raise SystemExit(
        f"Missing analysis dependency: {exc}. Install with: "
        "python3 -m pip install --user 'librosa>=0.10.2,<0.12' 'scipy>=1.11,<2'"
    ) from exc

SR = 22050
HOP = 256
FFT = 1024
MOD_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO_DIR = MOD_ROOT / 'tools' / 'mari_source'
SONGS = (
    ('Pink Horizon', 'pink-horizon', 'mari-rooftop', 'mari_track1.mp3'),
    ('Beast Mode', 'beast-mode', 'mari-hangar', 'mari_track2.mp3'),
    ('Final Sync', 'final-sync', 'mari-impact', 'mari_track3.mp3'),
)
DIFFICULTIES = {'easy': ('-easy', 2.05), 'normal': ('', 2.55), 'hard': ('-hard', 3.0)}


@dataclass(frozen=True)
class Candidate:
    time: float
    strength: float
    beat_index: int
    subdivision: int  # 0=beat, 2=half, 1/3=quarter, 5/6=thirds, -1=fallback


@dataclass
class Analysis:
    duration: float
    beats: np.ndarray
    onsets: np.ndarray
    candidates: list[Candidate]
    boundary: list[float]
    bpms: list[float]
    tracking: str
    fit: float
    global_fit: float
    dynamic_fit: float
    median_bpm: float
    rms_threshold: float


def run(command: list[str], *, capture: bool = False) -> bytes:
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        return result.stdout if capture else b''
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{' '.join(command[:4])} failed: {exc.stderr.decode(errors='replace')[-2000:]}") from exc


def convert_and_decode(source: Path, ogg: Path) -> np.ndarray:
    run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
         '-map', '0:a:0', '-vn', '-af', 'volume=0.75', '-c:a', 'libvorbis', '-q:a', '6', str(ogg)])
    # Analyze the actual converted audio, so the chart follows the game's file,
    # including decoder delay/padding, rather than a differently decoded MP3.
    raw = run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(ogg),
               '-map', '0:a:0', '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-acodec', 'pcm_f32le', '-'],
              capture=True)
    y = np.frombuffer(raw, dtype='<f4').copy()
    if len(y) < SR * 5 or not np.isfinite(y).all() or float(np.max(np.abs(y))) < 1e-5:
        raise ValueError(f'{source}: audio is silent, corrupt, or shorter than 5 seconds')
    return y


def alignment(beats: np.ndarray, onset_times: np.ndarray, rms: np.ndarray, rms_times: np.ndarray,
              rms_threshold: float) -> float:
    if len(beats) < 5 or len(onset_times) < 5:
        return 0.0
    active = beats[np.interp(beats, rms_times, rms, left=0, right=0) > rms_threshold]
    if len(active) < 5:
        return 0.0
    distances = cKDTree(onset_times[:, None]).query(active[:, None])[0]
    return float(np.mean(distances <= 0.070))


def regularity(beats: np.ndarray) -> float:
    if len(beats) < 6:
        return 1.0
    delta = np.diff(beats)
    median = float(np.median(delta))
    return float(np.median(np.abs(delta - median)) / max(median, 1e-6))


def make_boundaries(beats: np.ndarray, duration: float, bpm: float) -> tuple[list[float], list[float]]:
    """Four measured beats per section, with a zero-anchored first section.

    Psych's Conductor uses section BPM to locate sections. Each BPM below is
    240 / exact section length, so the accumulated boundaries follow beat time.
    Padded beats are only for metadata; they never generate note timestamps.
    """
    step = 60.0 / bpm
    grid = list(map(float, beats))
    if not grid:
        grid = [0.0]
    while grid[0] > step * 0.60:
        grid.insert(0, grid[0] - step)
    while grid[0] < -step * 0.50:
        grid.pop(0)
    while len(grid) < 5 or grid[-1] < duration + step * 4:
        grid.append(grid[-1] + step)
    boundaries = [0.0]
    index = 4
    while boundaries[-1] < duration:
        if index >= len(grid):
            grid.append(grid[-1] + step)
            continue
        end = grid[index]
        # A misdetected beat must not create impossibly short/long sections.
        length = end - boundaries[-1]
        if length < 4 * step * 0.68 or length > 4 * step * 1.40:
            end = boundaries[-1] + 4 * step
        boundaries.append(float(end))
        index += 4
    bpms = [240.0 / (b - a) for a, b in zip(boundaries, boundaries[1:])]
    return boundaries, bpms


def analyze(y: np.ndarray) -> Analysis:
    duration = len(y) / SR
    onset_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, n_fft=FFT, aggregate=np.mean)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=SR, hop_length=HOP,
                                              backtrack=False, units='frames')
    onset_times = librosa.frames_to_time(onset_frames, sr=SR, hop_length=HOP)
    if len(onset_times) < 12:
        raise ValueError('Too few detectable onsets; cannot safely construct a playable chart')
    rms = librosa.feature.rms(y=y, frame_length=FFT, hop_length=HOP)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=SR, hop_length=HOP)
    rms_threshold = max(0.001, float(np.percentile(rms, 95)) * 0.025)
    tempo, global_beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=SR, hop_length=HOP,
                                                  units='time', trim=False, start_bpm=120, tightness=60)
    base_bpm = float(np.asarray(tempo).reshape(-1)[0])
    if not (40 <= base_bpm <= 260) or len(global_beats) < 8:
        raise ValueError('Unable to estimate a plausible musical pulse')
    global_fit = alignment(global_beats, onset_times, rms, rms_times, rms_threshold)
    # Local tempo can jump to half/double time in quiet passages. Smooth it and
    # constrain it relative to the global estimate; compare alignment before use.
    dynamic_bpm = librosa.feature.tempo(onset_envelope=onset_env, sr=SR, hop_length=HOP, aggregate=None)
    dynamic_bpm = median_filter(np.asarray(dynamic_bpm), size=301, mode='nearest')
    dynamic_bpm = np.clip(dynamic_bpm, base_bpm * 0.75, base_bpm * 1.33)
    _, dynamic_beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=SR, hop_length=HOP,
                                               units='time', trim=False, bpm=dynamic_bpm, tightness=60)
    dynamic_fit = alignment(dynamic_beats, onset_times, rms, rms_times, rms_threshold)
    # Require a meaningful fit improvement and penalize highly jittery tracking.
    if dynamic_fit - 0.30 * regularity(dynamic_beats) > global_fit - 0.30 * regularity(global_beats) + 0.025:
        beats, fit, tracking = dynamic_beats, dynamic_fit, 'adaptive'
    else:
        beats, fit, tracking = global_beats, global_fit, 'global'
    beats = np.asarray(beats, dtype=float)
    beat_period = float(np.median(np.diff(beats)))
    median_bpm = 60.0 / beat_period
    if not (40 <= median_bpm <= 260):
        raise ValueError(f'Implausible median tempo {median_bpm:.1f}')
    if fit < 0.65:
        # Keep sections approximately musical, but never invent grid notes. This
        # fallback chooses salient, spaced measured onsets without phase tests.
        tracking += '+onset-fallback'
    boundary, bpms = make_boundaries(beats, duration, median_bpm)

    peak = max(float(np.percentile(onset_env, 95)), 1e-8)
    candidates = []
    for frame, time in zip(onset_frames, onset_times):
        t = float(time)
        # Avoid notes before the track begins or too close to its audible end.
        if t < 0.12 or t > duration - 0.22:
            continue
        if float(np.interp(t, rms_times, rms, left=0, right=0)) < rms_threshold:
            continue
        strength = float(onset_env[int(frame)]) / peak
        # Fractional phase is measured between the two locally tracked beats.
        i = int(np.searchsorted(beats, t, side='right') - 1)
        if i < 0 or i >= len(beats) - 1:
            if 'fallback' not in tracking:
                continue
            candidates.append(Candidate(t, strength, max(0, i), -1))
            continue
        interval = float(beats[i + 1] - beats[i])
        if interval <= 0:
            continue
        phase = (t - float(beats[i])) / interval
        q = int(round(phase * 4))
        error = abs(phase - q / 4.0) * interval
        triplet = 5 if abs(phase - 1 / 3) <= abs(phase - 2 / 3) else 6
        triplet_phase = 1 / 3 if triplet == 5 else 2 / 3
        triplet_error = abs(phase - triplet_phase) * interval
        if 'fallback' in tracking:
            candidates.append(Candidate(t, strength, i, -1))
        elif triplet_error <= min(0.035, interval * 0.055) and triplet_error < error:
            candidates.append(Candidate(t, strength, i, triplet))
        elif error <= min(0.075, interval * 0.17):
            candidates.append(Candidate(t, strength, i + (q == 4), q % 4))
    if len(candidates) < 16:
        raise ValueError('Too few audio-aligned note candidates after analysis')
    return Analysis(duration, beats, onset_times, candidates, boundary, bpms, tracking, fit,
                    global_fit, dynamic_fit, median_bpm, rms_threshold)


# Each two-bar call is answered by a mirrored version of the same motif.
MOTIFS = (
    (0, 1, 2, 3, 2, 1, 0, 2),
    (0, 2, 1, 3, 1, 2, 0, 3),
    (1, 3, 2, 0, 2, 3, 1, 0),
    (3, 2, 0, 1, 0, 2, 3, 1),
    (0, 3, 1, 2, 3, 0, 2, 1),
)


def stable_seed(slug: str) -> int:
    return int.from_bytes(hashlib.sha256(slug.encode()).digest()[:4], 'big')


def select_events(analysis: Analysis, difficulty: str) -> list[Candidate]:
    chosen = []
    last_time = -100.0
    seen_beats = set()
    fallback = 'fallback' in analysis.tracking
    min_gap = {'easy': 0.30, 'normal': 0.16, 'hard': 0.105}[difficulty]
    # In fallback, relative strength thresholds control density without relying
    # on a questionable beat grid. They are measured from this recording.
    strengths = np.asarray([x.strength for x in analysis.candidates])
    thresholds = {'easy': float(np.percentile(strengths, 68)),
                  'normal': float(np.percentile(strengths, 38)), 'hard': 0.0}
    for c in analysis.candidates:
        if fallback:
            allowed = c.strength >= thresholds[difficulty]
        elif difficulty == 'easy':
            allowed = c.subdivision == 0 and c.beat_index % 4 != 3 and c.beat_index not in seen_beats
        elif difficulty == 'normal':
            allowed = (c.subdivision == 0
                       or (c.subdivision == 2 and c.beat_index % 4 != 3)
                       # When a track uses thirds, alternate between one-third
                       # and two-thirds so Normal preserves its swing without
                       # becoming a full triplet stream.
                       or (c.subdivision in (5, 6) and c.subdivision - 5 == c.beat_index % 2))
        else:
            allowed = True
        if not allowed or c.time - last_time < min_gap:
            continue
        chosen.append(c)
        last_time = c.time
        if difficulty == 'easy':
            seen_beats.add(c.beat_index)
    return chosen


def make_chart(title: str, slug: str, stage: str, analysis: Analysis, difficulty: str) -> tuple[dict, dict]:
    selected = select_events(analysis, difficulty)
    sections = []
    per_section_indices = [0] * len(analysis.bpms)
    for i, bpm in enumerate(analysis.bpms):
        sections.append({'sectionNotes': [], 'sectionBeats': 4, 'lengthInSteps': 16,
                         'mustHitSection': bool((i // 2) % 2), 'gfSection': False,
                         'altAnim': False, 'bpm': round(bpm, 6), 'changeBPM': i != 0,
                         'typeOfSection': 0})
    seed = stable_seed(slug)
    last_lane = {False: -1, True: -1}
    last_chord_time = -100.0
    chord_count = 0
    for c in selected:
        i = int(np.searchsorted(analysis.boundary, c.time, side='right') - 1)
        i = max(0, min(i, len(sections) - 1))
        sec = sections[i]
        must = sec['mustHitSection']
        # The 4-bar (opponent, player) unit shares a motif; player mirrors it.
        motif = MOTIFS[(seed + i // 4) % len(MOTIFS)]
        pos = per_section_indices[i] + (i % 2) * 4
        lane = motif[pos % len(motif)]
        if must:
            lane = 3 - lane
        if lane == last_lane[must] and len(sec['sectionNotes']) and c.time - sec['sectionNotes'][-1][0] / 1000 < 0.27:
            lane = (lane + 1 + ((pos + seed) % 2)) % 4
        # In Psych, lanes 0..3 belong to the section's mustHitSection side.
        sec['sectionNotes'].append([round(c.time * 1000, 2), lane, 0.0])
        last_lane[must] = lane
        per_section_indices[i] += 1
        # Sparse two-key accents on strong beats only, never triples.
        if (difficulty == 'hard' and c.subdivision == 0 and c.strength >= 0.55
                and pos % 8 == 4 and c.time - last_chord_time > 1.8):
            second = (lane + 2) % 4
            sec['sectionNotes'].append([round(c.time * 1000, 2), second, 0.0])
            last_chord_time = c.time
            chord_count += 1

    # Short, occasional holds only when there is an actual sufficiently long
    # gap to the next note and to the section/song boundary. No hold overlaps
    # a later note, including chords; avoids impossible same-lane input.
    hold_count = 0
    hold_every = {'easy': 11, 'normal': 15, 'hard': 19}[difficulty]
    note_counter = 0
    last_hold = -100.0
    hold_times = []
    holds_by_side = {False: 0, True: 0}
    hold_candidates = []
    for i, sec in enumerate(sections):
        notes = sec['sectionNotes']
        notes.sort(key=lambda n: (n[0], n[1]))
        for j, note in enumerate(notes):
            note_counter += 1
            t = note[0] / 1000
            if (j > 0 and notes[j - 1][0] == note[0]) or (j + 1 < len(notes) and notes[j + 1][0] == note[0]):
                continue
            next_t = notes[j + 1][0] / 1000 if j + 1 < len(notes) else analysis.boundary[i + 1]
            available = min(next_t - t - 0.12, analysis.boundary[i + 1] - t - 0.08,
                            analysis.duration - t - 0.22, 60.0 / analysis.bpms[i] * 0.85)
            if available >= 0.26:
                hold_candidates.append((note, t, available, sec['mustHitSection']))
                if note_counter % hold_every == 3 and t - last_hold >= 2.0:
                    note[2] = round(available * 1000, 2)
                    hold_count += 1
                    holds_by_side[sec['mustHitSection']] += 1
                    hold_times.append(t)
                    last_hold = t

    # Dense rhythms may skip every scheduled hold. Include at most one extra
    # per missing side when there is a genuinely safe gap, rather than placing
    # a sustain across a stream or inventing an extra onset.
    for side in (False, True):
        if holds_by_side[side]:
            continue
        eligible = [(note, t, available) for note, t, available, owner in hold_candidates
                    if owner == side and not note[2] and all(abs(t - h) >= 2.0 for h in hold_times)]
        if eligible:
            note, t, available = max(eligible, key=lambda item: item[2])
            note[2] = round(available * 1000, 2)
            hold_count += 1
            holds_by_side[side] += 1
            hold_times.append(t)

    _, speed = DIFFICULTIES[difficulty]
    song = {'song': title, 'notes': sections, 'events': [], 'bpm': round(analysis.bpms[0], 6),
            'needsVoices': False, 'speed': speed, 'player1': 'shinji', 'player2': 'mari',
            'player3': None, 'gfVersion': 'gf', 'stage': stage, 'validScore': True,
            'arrowSkin': '', 'splashSkin': '', 'gameOverChar': 'shinji-dead'}
    side_counts = [sum(len(s['sectionNotes']) for s in sections if s['mustHitSection'] == side) for side in (False, True)]
    notes = [n for s in sections for n in s['sectionNotes']]
    if min(side_counts) < 4 or len(notes) < 12:
        raise ValueError(f'{title} {difficulty}: not enough playable notes for both characters')
    for i, sec in enumerate(sections):
        for t, lane, hold in sec['sectionNotes']:
            seconds = t / 1000
            if not (0 <= lane <= 3 and 0 <= hold and seconds >= analysis.boundary[i] - 0.001
                    and seconds < analysis.boundary[i + 1] + 0.001
                    and seconds + hold / 1000 <= analysis.duration - 0.1):
                raise ValueError(f'{title} {difficulty}: invalid note {t, lane, hold} in section {i}')
    distances = cKDTree(analysis.onsets[:, None]).query(np.array([n[0] / 1000 for n in notes])[:, None])[0]
    stats = {'notes_total': len(notes), 'opponent_notes': side_counts[0], 'player_notes': side_counts[1],
             'holds': hold_count, 'chords': chord_count,
             'notes_within_35ms_of_onset': round(float(np.mean(distances <= 0.035)), 4),
             'last_note_end_seconds': round(max((n[0] + n[2]) / 1000 for n in notes), 3)}
    if stats['notes_within_35ms_of_onset'] < 0.99:
        raise ValueError(f'{title} {difficulty}: note/onset validation failed')
    return {'song': song}, stats


def all_targets(root: Path) -> list[Path]:
    targets = []
    for _, slug, _, _ in SONGS:
        targets.extend(root / 'data' / slug / f'{slug}{suffix}.json' for suffix, _ in DIFFICULTIES.values())
        targets.append(root / 'songs' / slug / 'Inst.ogg')
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--audio-dir', type=Path, default=DEFAULT_AUDIO_DIR, help='directory containing mari_track1-3.mp3')
    parser.add_argument('--output-root', type=Path, default=MOD_ROOT, help='mod root (default: parent of this tools directory)')
    parser.add_argument('--force', action='store_true', help='overwrite only the twelve named chart/Inst outputs')
    args = parser.parse_args()
    for program in ('ffmpeg',):
        if not shutil.which(program):
            parser.error(f'{program} must be installed and on PATH')
    sources = [args.audio_dir / x[3] for x in SONGS]
    missing = [str(p) for p in sources if not p.is_file() or p.stat().st_size == 0]
    if missing:
        parser.error('missing or empty inputs: ' + ', '.join(missing))
    targets = all_targets(args.output_root)
    existing = [p for p in targets if p.exists()]
    if existing and not args.force:
        parser.error('refusing to overwrite existing targets (use --force if intentional): ' + ', '.join(map(str, existing)))
    # Build all artifacts privately first; no game resources change on analysis failure.
    with tempfile.TemporaryDirectory(prefix='mari-charts-') as temp:
        temp_root = Path(temp)
        staged = []
        for title, slug, stage, filename in SONGS:
            print(f'Analyzing {title}: {args.audio_dir / filename}', flush=True)
            ogg = temp_root / f'{slug}.ogg'
            y = convert_and_decode(args.audio_dir / filename, ogg)
            a = analyze(y)
            charts = {}
            for difficulty, (suffix, _) in DIFFICULTIES.items():
                chart, stats = make_chart(title, slug, stage, a, difficulty)
                chart_file = temp_root / f'{slug}{suffix}.json'
                chart_file.write_text(json.dumps(chart, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
                staged.append((chart_file, args.output_root / 'data' / slug / f'{slug}{suffix}.json'))
                charts[difficulty] = stats
            if not (charts['easy']['notes_total'] < charts['normal']['notes_total'] < charts['hard']['notes_total']):
                raise ValueError(f'{title}: difficulty densities did not increase; inspect audio/analysis')
            staged.append((ogg, args.output_root / 'songs' / slug / 'Inst.ogg'))
            print(json.dumps({'song': title, 'duration_seconds': round(a.duration, 3),
                              'median_detected_bpm': round(a.median_bpm, 2), 'beat_tracking': a.tracking,
                              'beat_onset_fit_within_70ms': round(a.fit, 3),
                              'global_fit': round(a.global_fit, 3), 'adaptive_fit': round(a.dynamic_fit, 3),
                              'sections': len(a.bpms), 'detected_onsets': len(a.onsets), 'charts': charts},
                             ensure_ascii=False), flush=True)
        # Check a second time in case files appeared during analysis.
        if not args.force and any(dest.exists() for _, dest in staged):
            raise FileExistsError('A target appeared during analysis; no files were installed')
        for src, dest in staged:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Copy to the destination filesystem and replace atomically per file.
            fd, tmp = tempfile.mkstemp(prefix='.' + dest.name + '.', dir=dest.parent)
            try:
                with os.fdopen(fd, 'wb') as out, src.open('rb') as inp:
                    shutil.copyfileobj(inp, out)
                if args.force:
                    os.replace(tmp, dest)
                else:
                    # Atomic create-if-absent, unlike an exists()+replace
                    # pair which could overwrite a concurrent writer.
                    os.link(tmp, dest)
                    os.unlink(tmp)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            print(f'Wrote {dest}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
