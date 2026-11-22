#!/usr/bin/env python3
"""홀로그램 브리핑 음성(mp3) 생성 · 타임라인 정렬 스크립트.

매일 아침 브리핑 갱신 절차:
  1. data/briefing-hologram.json 의 date/weekday/as_of/stages[].{headline,sub,metrics,caption,speech} 를 오늘 내용으로 고친다.
  2. 이 스크립트를 실행한다.  (pip install edge-tts mutagen 필요)
  3. 생성된 data/briefing-<date>.mp3 와 갱신된 JSON 을 함께 커밋한다.

스크립트가 하는 일
  - stages[].speech 를 edge-tts(ko-KR 신경망 음성)로 문단별 mp3 로 만든다.
  - 문단별 실제 길이를 재서 stages[].start / .end 와 audio.duration 을 채운다(초 단위).
  - 문단 mp3 들을 ID3 태그 없이 이어 붙여 audio.src 한 파일로 만든다.

사용:
  python3 scripts/build_hologram_audio.py                    # 기본 data/briefing-hologram.json
  python3 scripts/build_hologram_audio.py --voice ko-KR-SunHiNeural --rate +4%
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile

import edge_tts
from mutagen.mp3 import MP3


def strip_tags(data: bytes) -> bytes:
    """ID3v2(앞)·ID3v1(뒤) 태그를 떼고 순수 MPEG 프레임만 남긴다."""
    if data[:3] == b"ID3" and len(data) > 10:
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        data = data[10 + size:]
    if data[-128:-125] == b"TAG":
        data = data[:-128]
    return data


def duration_of(path: str) -> float:
    return float(MP3(path).info.length)


async def synth(text: str, voice: str, rate: str, out_path: str) -> None:
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out_path)


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(here, "data", "briefing-hologram.json"))
    ap.add_argument("--voice", default=None, help="기본값은 JSON 의 voice 필드")
    ap.add_argument("--rate", default="+8%")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        doc = json.load(f)

    voice = args.voice or doc.get("voice") or "ko-KR-InJoonNeural"
    stages = doc["stages"]
    tmpdir = tempfile.mkdtemp(prefix="holo-tts-")

    durations: list[float] = []
    frames: list[bytes] = []
    for i, stage in enumerate(stages):
        text = (stage.get("speech") or "").strip()
        if not text:
            raise SystemExit(f"stage {i} 에 speech 가 없습니다.")
        path = os.path.join(tmpdir, f"{i:02d}.mp3")
        asyncio.run(synth(text, voice, args.rate, path))
        durations.append(duration_of(path))
        with open(path, "rb") as f:
            frames.append(strip_tags(f.read()))
        print(f"  {stage.get('no','??')} {stage.get('badge',''):<6} {durations[-1]:6.2f}s  {text[:34]}")

    # 0.25초씩 숨 고르기: 문단 사이 무음 프레임을 만들 수 없으므로(ffmpeg 없음)
    # 실제 무음 대신 다음 문단이 곧바로 이어진다. 대신 caption 전환은 start 시각에 맞춘다.

    out_mp3 = os.path.join(here, "data", f"briefing-{doc['date']}.mp3")
    blob = b"".join(frames)
    with open(out_mp3, "wb") as f:
        f.write(blob)
    total = duration_of(out_mp3)

    # 타임라인 채우기 — 각 문단 시작/끝(초)
    t = 0.0
    for stage, d in zip(stages, durations):
        stage["start"] = round(t, 2)
        stage["end"] = round(t + d, 2)
        t += d

    doc["voice"] = voice
    doc["audio"] = {
        "src": f"data/briefing-{doc['date']}.mp3",
        "duration": round(total, 2),
        "bytes": len(blob),
        "segments": [round(d, 2) for d in durations],
        "format": "mp3 · mono · 문단별 합본",
    }

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\nvoice={voice}  문단 {len(stages)}개  합계 {total:.2f}s  파일 {out_mp3} ({len(blob)/1024:.0f} KB)")
    if abs(total - sum(durations)) > 0.75:
        print(f"⚠ 합본 길이({total:.2f}s)와 문단 합({sum(durations):.2f}s)이 어긋납니다. 타임라인을 확인하세요.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
