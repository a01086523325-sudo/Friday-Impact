#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
마리 위크 컷씬 대사를 한국어 AI 음성(.ogg)으로 만들어 sounds/cutscene/ 에 저장합니다.
- '따따라와'로 발음이 꼬이는 현상을 방지하기 위해 문장 사이에 호흡(구분점)을 추가했습니다.
"""
import asyncio
import os
import subprocess
import tempfile

# 경로 설정
HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
OUT = os.path.join(MOD, 'sounds', 'cutscene')

# 대사 데이터 목록 (발음 버그 수정 버전)
SLUG = "pink-horizon"
LINES = [
    ('mari', '이야호오~, 혼자 노래하고 있었어? 강아지 군.'),
    ('shinji', '으앗… 언제부터 거기 있었어?'),
    ('mari', '방금 왔지. 저 분홍빛 하늘 봐! 노래하기 딱 좋은 날이잖아.'),
    ('shinji', '같이 부르자는 거야? 난 아직 준비가 안 됐는데…'),
    # 💡 '테니까' 뒤에 마침표(.)를 찍어 호흡을 완전히 분리함으로써 '따따라와' 버그를 해결했습니다.
    ('mari', '괜찮아, 박자만 타면 돼! 내가 먼저 시작할 테니까. 따라와.'),
    ('shinji', '알았어. 이번에는 도망치지 않을게!')
]

# 캐릭터 보이스 프리셋
VOICES = {
    'shinji': ('ko-KR-InJoonNeural', '+10Hz', '-8%'),
    'mari':   ('ko-KR-SunHiNeural',  '+11Hz', '+2%'),
}

def clean(text):
    return text.replace('……', ', ').replace('…', ', ').strip()

async def synth(text, voice, pitch, rate, mp3):
    import edge_tts
    await edge_tts.Communicate(clean(text), voice, rate=rate, pitch=pitch).save(mp3)

def main():
    os.makedirs(OUT, exist_ok=True)
    print("🎵 마리 위크 AI 보이스 생성 시작 (발음 교정 버전)...")
    
    for idx, (who, text) in enumerate(LINES, start=1):
        out_file = os.path.join(OUT, f'{SLUG}_{idx}.ogg')
        print(f' -> [{who}] {text} -> {SLUG}_{idx}.ogg')
        
        voice, pitch, rate = VOICES.get(who, VOICES['shinji'])
        
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = os.path.join(tmp, 'v.mp3')
            asyncio.run(synth(text, voice, pitch, rate, mp3))
            subprocess.run([
                'ffmpeg', '-y', '-loglevel', 'error', '-i', mp3, 
                '-ac', '1', '-c:a', 'libvorbis', '-q:a', '5', out_file
            ], check=True)
            
    print('\n✨ 완료: 더듬는 발음이 교정된 음성이 새로 저장되었습니다!')

if __name__ == '__main__':
    main()
