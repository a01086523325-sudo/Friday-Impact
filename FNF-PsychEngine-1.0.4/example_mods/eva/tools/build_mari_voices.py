#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create approximate FNF-style, wordless synthesizer vocals for three Mari songs.

Requires Python 3, numpy, scipy, soundfile (with Ogg/Vorbis support), and ffmpeg
with libvorbis. Run after the chart/instrumental builder has finished:
    python3 tools/build_mari_voices.py
    python3 tools/build_mari_voices.py --root /path/to/mod

Normal charts supply all vocal onsets; easy and hard share the same stem, as in
Psych Engine. This synthesizes vowel-like tones, NOT real actors or sung words.
The key/chord detector is heuristic; listen and adjust in a DAW if necessary.
All inputs are checked and all twelve replacements staged before installation.
Caught errors during installation trigger best-effort rollback. A power loss,
SIGKILL, disk failure, or concurrent third-party writes cannot be made into a
truly atomic transaction across twelve separate files; keep a backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from scipy import ndimage, signal

SLUGS = ("pink-horizon", "beast-mode", "final-sync")
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# F1/F2: vowel colors, not speech samples. Different formants and registers
# deliberately make the two fictional characters distinguishable.
VOICES = {
    "shinji": {"base": 59, "vowels": ((470, 1150), (570, 1350), (430, 1600), (620, 1100)),
               "fm": 0.08, "pan": -0.10},
    "mari": {"base": 67, "vowels": ((620, 1700), (500, 1950), (720, 1500), (550, 1800)),
             "fm": 0.13, "pan": 0.10},
}


@dataclass(frozen=True)
class Note:
    seconds: float
    lane: int
    sustain: float
    actor: str


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def character(value: object, field: str, path: Path) -> str:
    name = str(value or "").lower()
    found = [c for c in VOICES if c in name]
    if len(found) != 1:
        raise ValueError(f"{path}: {field} must identify shinji or mari, got {value!r}")
    return found[0]


def chart_song(doc: object, path: Path) -> dict:
    if not isinstance(doc, dict) or not isinstance(doc.get("song"), dict):
        raise ValueError(f"{path}: expected a Psych Engine {{'song': {{...}}}} chart")
    return doc["song"]


def read_charts(root: Path):
    """Read every requested input once, before creating or modifying any output."""
    charts = {}
    snapshots = {}
    for slug in SLUGS:
        for suffix in ("", "-easy", "-hard"):
            path = root / "data" / slug / f"{slug}{suffix}.json"
            raw = path.read_bytes()
            doc = json.loads(raw.decode("utf-8-sig"))
            song = chart_song(doc, path)
            if not isinstance(song.get("notes"), list):
                raise ValueError(f"{path}: song.notes must be a list")
            charts[(slug, suffix)] = (path, doc)
            snapshots[path] = digest(raw)
        inst = root / "songs" / slug / "Inst.ogg"
        if not inst.is_file():
            raise FileNotFoundError(inst)
        if not (root / "songs" / slug).is_dir():
            raise FileNotFoundError(inst.parent)
    return charts, snapshots


def extract_notes(song: dict, path: Path, duration: float) -> list[Note]:
    player = character(song.get("player1"), "player1", path)
    opponent = character(song.get("player2"), "player2", path)
    if player == opponent:
        raise ValueError(f"{path}: player1 and player2 must identify different actors")
    result = []
    for section_number, section in enumerate(song["notes"]):
        if not isinstance(section, dict) or not isinstance(section.get("mustHitSection"), bool):
            raise ValueError(f"{path}: section {section_number} needs boolean mustHitSection")
        entries = section.get("sectionNotes")
        if not isinstance(entries, list):
            raise ValueError(f"{path}: section {section_number} needs sectionNotes list")
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 3:
                raise ValueError(f"{path}: bad note {entry!r}")
            try:
                milliseconds = float(entry[0])
                lane_value = float(entry[1])
                sustain_ms = float(entry[2])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: bad note {entry!r}") from exc
            if (not all(math.isfinite(v) for v in (milliseconds, lane_value, sustain_ms))
                    or not lane_value.is_integer() or not 0 <= lane_value <= 7
                    or milliseconds < 0 or sustain_ms < 0):
                raise ValueError(f"{path}: invalid note {entry!r}; expected time >= 0 and lane 0..7")
            time = milliseconds / 1000.0
            if time >= duration:
                raise ValueError(f"{path}: note at {time:.3f}s outside Inst duration {duration:.3f}s")
            lane = int(lane_value)
            # Psych Engine: lanes 4..7 invert the section's usual side.
            is_player = section["mustHitSection"] != (lane >= 4)
            result.append(Note(time, lane % 4, sustain_ms / 1000.0,
                               player if is_player else opponent))
    if not result:
        raise ValueError(f"{path}: no vocal notes in the normal chart")
    return sorted(result, key=lambda n: (n.seconds, n.actor, n.lane))


def read_instrument(path: Path):
    info = sf.info(str(path))
    if info.samplerate < 8000 or info.samplerate > 192000 or info.channels not in (1, 2):
        raise ValueError(f"{path}: unsupported rate/channels: {info.samplerate}, {info.channels}")
    if not 0 < info.frames <= info.samplerate * 600:
        raise ValueError(f"{path}: empty or over 10 minutes; refusing excessive memory/output size")
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if len(data) != info.frames or not np.isfinite(data).all():
        raise ValueError(f"{path}: incomplete or non-finite decoded samples")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(data)))
    if rms < 1e-5:
        raise ValueError(f"{path}: instrumental is effectively silent, cannot infer harmony/level")
    return data, sr, rms, peak


def harmony(inst: np.ndarray, sr: int, bpm: float):
    """Estimate global major/minor and beat-sized local diatonic triads.

    Spectral chroma is deliberately approximate: no claim of separating drums,
    recognizing modulations perfectly, or recovering a source melody.
    """
    mono = np.mean(inst, axis=1)
    target = 11025
    gcd = math.gcd(sr, target)
    mono = signal.resample_poly(mono, target // gcd, sr // gcd)
    freq, times, spec = signal.stft(mono, fs=target, nperseg=4096, noverlap=3072,
                                    boundary=None, padded=False)
    if len(times) == 0:
        raise ValueError("Instrumental is too short for harmony analysis")
    valid = (freq >= 65.0) & (freq <= 1600.0)
    bins = np.flatnonzero(valid)
    pcs = np.rint(69 + 12 * np.log2(freq[bins] / 440.0)).astype(int) % 12
    # A mild low-frequency emphasis limits shrill percussion/high overtones.
    magnitudes = np.sqrt(np.abs(spec[bins])) / np.power(freq[bins, None] / 110.0, 0.28)
    chroma = np.zeros((12, len(times)), dtype=np.float64)
    for pc in range(12):
        chroma[pc] = np.sum(magnitudes[pcs == pc], axis=0)
    global_chroma = np.mean(chroma, axis=1)
    def correlate(a, b):
        a = a - np.mean(a)
        b = b - np.mean(b)
        return float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12))
    choices = [(correlate(global_chroma, np.roll(profile, root)), root, mode)
               for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE))
               for root in range(12)]
    confidence, root, mode = max(choices)
    scale = (0, 2, 4, 5, 7, 9, 11) if mode == "major" else (0, 2, 3, 5, 7, 8, 10)
    scale_pcs = tuple((root + x) % 12 for x in scale)
    # Two beats per window provides a simple chord progression without treating
    # a single noisy FFT frame as a chord. Seven in-key triads compete.
    window = max(0.5, min(2.5, 120.0 / bpm))
    count = max(1, int(math.ceil(len(inst) / sr / window)))
    chords = []
    global_norm = global_chroma / max(float(global_chroma.sum()), 1e-12)
    for index in range(count):
        mask = (times >= index * window) & (times < (index + 1) * window)
        local = np.mean(chroma[:, mask], axis=1) if np.any(mask) else global_chroma
        local = local / max(float(local.sum()), 1e-12)
        local = 0.82 * local + 0.18 * global_norm
        candidates = []
        for degree in range(7):
            triad = tuple(scale_pcs[(degree + shift) % 7] for shift in (0, 2, 4))
            score = 1.0 * local[triad[0]] + 0.92 * local[triad[1]] + 0.82 * local[triad[2]]
            candidates.append((score, triad))
        chords.append(max(candidates, key=lambda x: x[0])[1])
    return root, mode, confidence, scale_pcs, window, chords


def chosen_midi(note: Note, sequence: int, scale: tuple, chord: tuple) -> int:
    profile = VOICES[note.actor]
    base = profile["base"]
    # Lane controls melodic direction; subtle phrase variation avoids a fixed
    # four-note ringtone. Snap to the detected local chord to limit clashes.
    shape = (-2, 0, 2, 4)[note.lane] + (0, 1, 0, -1, 1, 0, 2, 0)[sequence % 8]
    target = base + shape
    candidates = [m for m in range(base - 7, base + 10) if m % 12 in chord]
    return min(candidates, key=lambda m: (abs(m - target), abs(m - base)))


def vowel(midi: int, duration: float, sr: int, actor: str, lane: int, seq: int) -> np.ndarray:
    count = max(1, int(round(duration * sr)))
    t = np.arange(count, dtype=np.float64) / sr
    profile = VOICES[actor]
    f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
    # A tiny initial slide and delayed vibrato give life without noisy buzzer FM.
    slide = 0.32 * np.exp(-t / 0.038) * (-1 if (seq % 2) else 1)
    vibrato = 0.10 * np.sin(2 * np.pi * 4.8 * t) * np.minimum(t / 0.14, 1.0)
    phase = 2 * np.pi * np.cumsum(f0 * np.power(2.0, (slide + vibrato) / 12.0)) / sr
    fm = profile["fm"] * np.sin(2 * phase)
    f1, f2 = profile["vowels"][(lane + seq // 4) % 4]
    waveform = np.zeros(count, dtype=np.float64)
    total = 0.0
    for harmonic in range(1, min(14, int(3300 / f0)) + 1):
        f = harmonic * f0
        formants = (0.35 + 1.10 * math.exp(-0.5 * ((f - f1) / 260) ** 2)
                    + 0.60 * math.exp(-0.5 * ((f - f2) / 420) ** 2))
        amp = formants / harmonic ** 1.12
        waveform += amp * np.sin(harmonic * phase + fm)
        total += amp
    waveform /= max(total, 1e-12)
    attack = min(0.014, duration * 0.24)
    release = min(0.045, duration * 0.32)
    envelope = np.ones(count, dtype=np.float64)
    if attack > 0:
        envelope *= np.sin(np.clip(t / attack, 0, 1) * np.pi / 2) ** 2
    if release > 0:
        envelope *= np.sin(np.clip((duration - t) / release, 0, 1) * np.pi / 2) ** 2
    envelope *= 0.87 + 0.13 * np.exp(-t / 0.18)
    return (waveform * envelope).astype(np.float32)


def render(notes: list[Note], inst: np.ndarray, sr: int, bpm: float, inst_rms: float):
    root, mode, confidence, scale, window, chords = harmony(inst, sr, bpm)
    print(f"    harmony: root={['C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B'][root]} "
          f"{mode}, correlation={confidence:.2f}", flush=True)
    stem = np.zeros_like(inst, dtype=np.float32)
    active = np.zeros(len(inst), dtype=bool)
    beat = 60.0 / bpm
    counts = {"shinji": 0, "mari": 0}
    # Work independently per side so simultaneous chords are not cut off by
    # each other. Exact onset duplicates are rendered as a quiet polyphonic group.
    for actor in ("shinji", "mari"):
        side = [n for n in notes if n.actor == actor]
        groups = []
        for note in side:
            if groups and abs(note.seconds - groups[-1][0].seconds) < 0.0005:
                groups[-1].append(note)
            else:
                groups.append([note])
        for group_index, group in enumerate(groups):
            next_gap = (groups[group_index + 1][0].seconds - group[0].seconds
                        if group_index + 1 < len(groups) else 10.0)
            # De-duplicate identical lane hits, keeping the longest sustain.
            unique = {}
            for note in group:
                if note.lane not in unique or note.sustain > unique[note.lane].sustain:
                    unique[note.lane] = note
            group_notes = list(unique.values())
            for note in group_notes:
                short = min(0.24, beat * 0.48)
                wanted = max(short, min(note.sustain + 0.055, 1.10)) if note.sustain else short
                length = min(wanted, max(0.012, next_gap * 0.91), len(inst) / sr - note.seconds)
                if length <= 0:
                    continue
                start = min(len(inst) - 1, int(round(note.seconds * sr)))
                pitch = chosen_midi(note, group_index, scale,
                                    chords[min(int(note.seconds / window), len(chords) - 1)])
                tone = vowel(pitch, length, sr, actor, note.lane, group_index)
                end = min(len(inst), start + len(tone))
                tone = tone[:end - start] / math.sqrt(len(group_notes))
                pan = VOICES[actor]["pan"]
                stem[start:end, 0] += tone * (1.0 - pan)
                stem[start:end, 1] += tone * (1.0 + pan)
                active[start:end] = True
                counts[actor] += 1
    if not active.any():
        raise ValueError("No audible notes could be rendered")
    active_rms = float(np.sqrt(np.mean(stem[active].astype(np.float64) ** 2)))
    if active_rms < 1e-9:
        raise ValueError("Synthesized stem is silent")
    # Set active RMS in proportion to the actual background. A conservative
    # sample peak ceiling also controls summed polyphony and lossy overshoots.
    target = min(inst_rms * 0.23, 0.045)
    peak = float(np.max(np.abs(stem)))
    gain = min(target / active_rms, 0.16 / max(peak, 1e-12))
    stem *= gain
    print(f"    notes: shinji={counts['shinji']} mari={counts['mari']}; "
          f"Inst RMS={inst_rms:.4f}, active voice RMS target<={target:.4f}", flush=True)
    return stem


def make_mix_safe(inst: np.ndarray, stem: np.ndarray, sr: int):
    """Reduce voice around hot backing transients, smoothly and with lookahead.

    Where the backing itself exceeds the headroom target, don't make it louder.
    This cannot repair pre-existing clipping in the instrumental.
    """
    limit = np.maximum(0.96, np.abs(inst))
    allowed = np.ones(len(inst), dtype=np.float32)
    for c in range(2):
        v = stem[:, c]
        x = inst[:, c]
        bounds = np.ones(len(inst), dtype=np.float32)
        pos, neg = v > 1e-9, v < -1e-9
        bounds[pos] = np.minimum(1.0, (limit[:, c][pos] - x[pos]) / v[pos])
        bounds[neg] = np.minimum(1.0, (-limit[:, c][neg] - x[neg]) / v[neg])
        allowed = np.minimum(allowed, np.clip(bounds, 0.0, 1.0))
    # Minimum lookahead exceeds half the smoothing kernel, so smoothed gain
    # still cannot rise above the instantaneous safe bound.
    size = max(3, int(sr * 0.020) | 1)
    smooth = max(3, int(sr * 0.008) | 1)
    allowed = ndimage.minimum_filter1d(allowed, size=size, mode="nearest")
    allowed = ndimage.uniform_filter1d(allowed, size=smooth, mode="nearest")
    stem *= allowed[:, None]


def temporary_sibling(path: Path, extension: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=".mari-voices-", suffix=extension, dir=path.parent)
    os.close(fd)
    return Path(name)


def encode(stem: np.ndarray, inst: np.ndarray, sr: int, output: Path, work: Path):
    wav = work / "render.wav"
    # Write a floating WAV as input to lossy encoding, never overwrite target.
    sf.write(str(wav), stem, sr, format="WAV", subtype="FLOAT")
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-map_metadata", "-1",
               "-c:a", "libvorbis", "-q:a", "3", "-ar", str(sr), "-ac", "2", str(output)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    decoded, decoded_sr = sf.read(str(output), dtype="float32", always_2d=True)
    if decoded_sr != sr or decoded.shape != stem.shape or not np.isfinite(decoded).all():
        raise ValueError(f"encoded Ogg mismatch: {decoded_sr} Hz, {decoded.shape}, "
                         f"expected {sr} Hz, {stem.shape}")
    peak = float(np.max(np.abs(decoded)))
    mix_peak = float(np.max(np.abs(decoded + inst)))
    if peak >= 0.98:
        raise ValueError(f"encoded vocal would clip (peak {peak:.4f})")
    # Already-hot backing samples are outside our control; flag only new clips.
    newly_clipped = np.any((np.abs(inst) < 1.0) & (np.abs(decoded + inst) >= 1.0))
    if newly_clipped:
        raise ValueError("encoded mix would introduce clipping; originals left untouched")
    max_size = min(32 * 1024 * 1024, max(1024 * 1024, int(len(stem) / sr * 25000)))
    if output.stat().st_size > max_size:
        raise ValueError(f"Ogg unexpectedly large ({output.stat().st_size} bytes > {max_size})")
    if peak < 1e-7:
        raise ValueError("encoded vocal is silent")
    print(f"    encoded: {len(decoded) / sr:.3f}s, stereo {sr} Hz, "
          f"voice peak {peak:.4f}, mix peak {mix_peak:.4f}, {output.stat().st_size} bytes", flush=True)


def commit(staged: dict[Path, Path], snapshots: dict[Path, str], work: Path):
    """Atomically replace each file; restore earlier replacements on exceptions."""
    for path, old_hash in snapshots.items():
        if not path.is_file() or digest(path.read_bytes()) != old_hash:
            raise RuntimeError(f"Input changed during generation: {path}")
    # Preserve all current targets for rollback before the first replacement.
    originals = {}
    for idx, path in enumerate(staged):
        if path.is_symlink():
            raise ValueError(f"Refusing to replace a symlink: {path}")
        if path.exists():
            if not path.is_file():
                raise ValueError(f"Target is not a regular file: {path}")
            backup = work / f"backup-{idx}"
            shutil.copy2(path, backup)
            originals[path] = backup
            os.chmod(staged[path], stat.S_IMODE(path.stat().st_mode))
        else:
            originals[path] = None
            os.chmod(staged[path], 0o644)
    installed = []
    try:
        for path, stage in staged.items():
            # os.replace is atomic per file, because the stage is in its parent.
            os.replace(stage, path)
            installed.append(path)
    except BaseException as exc:
        errors = []
        for path in reversed(installed):
            try:
                backup = originals[path]
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    restore = temporary_sibling(path, ".restore")
                    try:
                        shutil.copy2(backup, restore)
                        os.replace(restore, path)
                    finally:
                        restore.unlink(missing_ok=True)
            except Exception as restore_exc:
                errors.append(f"{path}: {restore_exc}")
        if errors:
            raise RuntimeError("Commit failed and rollback was incomplete: " + "; ".join(errors)) from exc
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="mod root (default: parent of this script's tools directory)")
    args = parser.parse_args()
    root = args.root.resolve()
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg with libvorbis is required on PATH")
    charts, snapshots = read_charts(root)
    staged = {}
    try:
        with tempfile.TemporaryDirectory(prefix="mari-voices-work-") as workdir:
            work = Path(workdir)
            for slug in SLUGS:
                path, doc = charts[(slug, "")]
                song = chart_song(doc, path)
                try:
                    bpm = float(song["bpm"])
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValueError(f"{path}: missing/invalid bpm") from exc
                if not math.isfinite(bpm) or not 30 <= bpm <= 400:
                    raise ValueError(f"{path}: unreasonable bpm {bpm!r}")
                inst_path = root / "songs" / slug / "Inst.ogg"
                print(f"{slug}: analyzing {inst_path}", flush=True)
                # Hash the instrumental as well: don't commit voices based on a
                # backing track replaced by another builder while we're running.
                snapshots[inst_path] = digest(inst_path.read_bytes())
                inst, sr, inst_rms, inst_peak = read_instrument(inst_path)
                notes = extract_notes(song, path, len(inst) / sr)
                if inst_peak >= 1:
                    print(f"    warning: decoded instrumental already peaks at {inst_peak:.4f}", flush=True)
                stem = render(notes, inst, sr, bpm, inst_rms)
                make_mix_safe(inst, stem, sr)
                target = root / "songs" / slug / "Voices.ogg"
                stage = temporary_sibling(target, ".ogg")
                staged[target] = stage
                encode(stem, inst, sr, stage, work)
            # Only change needsVoices; the rest of each parsed JSON is preserved.
            for slug in SLUGS:
                for suffix in ("", "-easy", "-hard"):
                    path, doc = charts[(slug, suffix)]
                    chart_song(doc, path)["needsVoices"] = True
                    stage = temporary_sibling(path, ".json")
                    staged[path] = stage
                    with stage.open("w", encoding="utf-8", newline="\n") as f:
                        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                        f.write("\n")
            commit(staged, snapshots, work)
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
    print("Done: three Voices.ogg files and all nine chart needsVoices flags installed.", flush=True)
    print("Synthetic vowel/FM tones only; not actual voice-actor singing.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
