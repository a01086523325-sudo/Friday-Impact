#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
치트 방지 대사(scripts/cheat_asuka.lua 의 CAST 표 msg)를 edge-tts 한국어 음성(.ogg)으로 만들어
sounds/cheat/<캐릭터>.ogg 에 저장합니다. 파일이 있으면 게임에서 자동으로 이 음성이 재생됩니다.
(없으면 아스카는 sounds/asuka_bad_user_fast.ogg, 나머지는 말소리 효과음만 납니다)

준비 (인터넷 연결 필요, 한 번만):
    pip install edge-tts
    ffmpeg 가 설치되어 있고 PATH 에 있어야 합니다.

사용:
    python tools/make_cheat_voice.py              # 없는 파일만 생성 (아스카는 기존 클립이 있어 제외)
    python tools/make_cheat_voice.py --all        # 아스카도 새로 생성
    python tools/make_cheat_voice.py --force      # 전부 다시 생성
    python tools/make_cheat_voice.py --dry-run    # 대사 목록과 파일 이름만 출력
    python tools/make_cheat_voice.py --blips      # 말소리 효과음(sounds/cheat/blip_*.ogg)만 다시 생성

음성은 QUIT_SECONDS(기본 3초) 안에 끝나도록 앞뒤 무음을 자르고, 길면 속도를 높여(최대 1.7배) 맞춥니다.
대사를 고치면 cheat_asuka.lua 의 msg 만 수정하고 이 스크립트를 다시 실행하세요.
"""
import asyncio, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
LUA = os.path.join(MOD, 'scripts', 'cheat_asuka.lua')
OUT = os.path.join(MOD, 'sounds', 'cheat')
CUT = os.path.join(MOD, 'sounds', 'cutscene')

# 캐릭터별 목소리 (edge-tts 한국어 음성). 필요하면 음성/높낮이(pitch)/속도(rate)를 바꿔 보세요.
VOICES = {
    'asuka': ('ko-KR-SunHiNeural',  '+12Hz', '+8%'),
    'rei':   ('ko-KR-SunHiNeural',  '-18Hz', '-12%'),
    'kaoru': ('ko-KR-InJoonNeural', '-4Hz',  '-5%'),
    'mari':  ('ko-KR-SunHiNeural',  '+6Hz',  '+8%'),
}
MAX_SPEEDUP = 1.7


def parse_cast(path):
    src = open(path, encoding='utf-8').read()
    quit_s = re.search(r'local\s+QUIT_SECONDS\s*=\s*([0-9.]+)', src)
    quit_s = float(quit_s.group(1)) if quit_s else 3.0
    block = re.search(r'local\s+CAST\s*=\s*\{(.*?)\n\}', src, re.S)
    if not block:
        sys.exit('CAST 표를 찾지 못했습니다: ' + path)
    cast = {}
    for m in re.finditer(r'(\w+)\s*=\s*\{([^{}]*)\}', block.group(1)):
        body = m.group(2)
        msg = re.search(r"msg\s*=\s*'((?:[^'\\]|\\.)*)'", body)
        if msg:
            cast[m.group(1)] = (msg.group(1).replace("\\'", "'"), 'clip' in body)
    return cast, quit_s


def clean(text):
    return text.replace('……', ', ').replace('…', ', ').replace('~', '').strip()


def duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def fit_to_ogg(src, dst, target):
    """앞뒤 무음 제거 → 목표 길이보다 길면 속도를 높여 맞춤 → ogg 저장"""
    trim = 'silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse'
    with tempfile.TemporaryDirectory() as td:
        trimmed = os.path.join(td, 'trim.wav')
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-af', trim, trimmed], check=True)
        dur = duration(trimmed)
        filt = []
        if dur > target:
            tempo = min(MAX_SPEEDUP, dur / target)
            filt = ['-af', 'atempo=%.3f' % tempo]
            print('    %.2f초 → 속도 x%.2f' % (dur, tempo))
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', trimmed] + filt + ['-c:a', 'libvorbis', '-q:a', '5', dst], check=True)
        print('    저장: %s (%.2f초)' % (os.path.relpath(dst, MOD), duration(dst)))


async def synth(text, voice, pitch, rate, mp3):
    import edge_tts
    await edge_tts.Communicate(clean(text), voice, rate=rate, pitch=pitch).save(mp3)


def make_blips():
    """말소리 효과음: 컷씬 babble 을 0.45 볼륨으로 줄여 sounds/cheat/blip_<캐릭터>_<1~4>.ogg 로 저장.
    마리는 아스카의 babble 을 높은 음으로 바꿔 사용합니다."""
    os.makedirs(OUT, exist_ok=True)
    for who, src_who, pitch in (('asuka', 'asuka', 1.0), ('rei', 'rei', 1.0), ('kaoru', 'kaoru', 1.0), ('mari', 'asuka', 1.18)):
        for n in range(1, 5):
            src = os.path.join(CUT, 'babble_%s_%d.ogg' % (src_who, n))
            dst = os.path.join(OUT, 'blip_%s_%d.ogg' % (who, n))
            if not os.path.exists(src):
                print('  (없음) ' + src); continue
            af = 'volume=0.45'
            if pitch != 1.0:
                sr = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=sample_rate',
                                     '-of', 'csv=p=0', src], capture_output=True, text=True, check=True).stdout.strip()
                af = 'asetrate=%d,aresample=%s,atempo=%.4f,volume=0.45' % (int(int(sr) * pitch), sr, 1 / pitch)
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-af', af, '-c:a', 'libvorbis', '-q:a', '4', dst], check=True)
    print('말소리 효과음 생성 완료:', OUT)


def main():
    if '--blips' in sys.argv:
        return make_blips()
    force, dry, all_ = '--force' in sys.argv, '--dry-run' in sys.argv, '--all' in sys.argv
    cast, quit_s = parse_cast(LUA)
    target = max(1.5, quit_s - 0.1)
    os.makedirs(OUT, exist_ok=True)
    todo = []
    for key, (msg, has_clip) in cast.items():
        dst = os.path.join(OUT, key + '.ogg')
        if has_clip and not (all_ or force):
            print('- %s: 기존 음성 클립 사용 (건너뜀, --all 로 새로 생성 가능)' % key); continue
        if os.path.exists(dst) and not force:
            print('- %s: 이미 있음 (건너뜀)' % key); continue
        todo.append((key, msg, dst))
    for key, msg, dst in todo:
        print('- %s: %s' % (key, msg))
    if dry or not todo:
        return
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        sys.exit('edge-tts 가 필요합니다:  pip install edge-tts')
    for key, msg, dst in todo:
        voice, pitch, rate = VOICES[key]
        print('생성 중: %s (%s)' % (key, voice))
        with tempfile.TemporaryDirectory() as td:
            mp3 = os.path.join(td, key + '.mp3')
            asyncio.run(synth(msg, voice, pitch, rate, mp3))
            fit_to_ogg(mp3, dst, target)
    print('완료. 게임에서 sounds/cheat/ 의 음성이 자동으로 재생됩니다.')


if __name__ == '__main__':
    main()
