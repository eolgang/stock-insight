#!/usr/bin/env python3
"""홀로그램 브리핑 음성(mp3) 생성 · 타임라인 정렬 스크립트.

매일 아침 브리핑 갱신 절차:
  1. data/briefing-hologram.json 의 date/weekday/as_of/stages[].{headline,sub,metrics,caption,speech} 를 오늘 내용으로 고친다.
  2. 이 스크립트를 실행한다.  (pip install edge-tts mutagen imageio-ffmpeg 필요)
  3. 생성된 data/briefing-<date>.mp3 와 갱신된 JSON 을 함께 커밋한다.

스크립트가 하는 일
  - stages[].speech 를 edge-tts(ko-KR 신경망 음성)로 문단별 mp3 로 만든다.
  - ffmpeg 가 있으면 문단 사이에 --gap 초 무음을 넣어 한 파일로 합친다(없으면 프레임 이어붙이기).
  - 문단별 실제 길이를 재서 stages[].start / .end 와 audio.duration 을 채운다(초 단위).

사용:
  python3 scripts/build_hologram_audio.py                       # 기본값: SunHi 음성 · rate +2% · pitch -2Hz · 간격 0.3s
  python3 scripts/build_hologram_audio.py --voice ko-KR-SunHiNeural --rate +1% --pitch -1Hz
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import edge_tts
from mutagen.mp3 import MP3

DEFAULT_VOICE = "ko-KR-SunHiNeural"
DEFAULT_RATE = "+2%"
DEFAULT_PITCH = "-2Hz"
DEFAULT_GAP = 0.30


def ffmpeg_exe() -> str | None:
    """ffmpeg 실행 파일 경로(시스템 → imageio-ffmpeg 번들 순)."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


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


async def synth(text: str, voice: str, rate: str, pitch: str, out_path: str) -> None:
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(out_path)


def to_wav(ff: str, src: str, dst: str) -> None:
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-i", src, "-ar", "24000", "-ac", "1", "-sample_fmt", "s16", dst],
        check=True,
    )


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(here, "data", "briefing-hologram.json"))
    ap.add_argument("--voice", default=None, help=f"기본값 {DEFAULT_VOICE}")
    ap.add_argument("--rate", default=DEFAULT_RATE)
    ap.add_argument("--pitch", default=DEFAULT_PITCH)
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP, help="문단 사이 무음(초), 0.25~0.4 권장")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        doc = json.load(f)

    voice = args.voice or doc.get("voice") or DEFAULT_VOICE
    stages = doc["stages"]
    tmpdir = tempfile.mkdtemp(prefix="holo-tts-")
    ff = ffmpeg_exe()

    durations: list[float] = []
    frames: list[bytes] = []
    wavs: list[str] = []
    for i, stage in enumerate(stages):
        text = (stage.get("speech") or "").strip()
        if not text:
            raise SystemExit(f"stage {i} 에 speech 가 없습니다.")
        path = os.path.join(tmpdir, f"{i:02d}.mp3")
        asyncio.run(synth(text, voice, args.rate, args.pitch, path))
        d = duration_of(path)
        durations.append(d)
        frames.append(strip_tags(open(path, "rb").read()))
        if ff:
            w = os.path.join(tmpdir, f"{i:02d}.wav")
            to_wav(ff, path, w)
            wavs.append(w)
        print(f"  {stage.get('no','??')} {stage.get('badge',''):<6} {d:6.2f}s  {text[:34]}")

    out_mp3 = os.path.join(here, "data", f"briefing-{doc['date']}.mp3")

    if ff and wavs:
        # 문단 사이에 실제 무음을 넣어 합친다 → 숨 고르는 자비스 리듬
        gap_frames = int(round(args.gap * 24000))
        merged = os.path.join(tmpdir, "merged.wav")
        with wave.open(wavs[0], "rb") as w0:
            params = w0.getparams()
        with wave.open(merged, "wb") as out:
            out.setnchannels(params.nchannels)
            out.setsampwidth(params.sampwidth)
            out.setframerate(params.framerate)
            silence = b"\x00" * (gap_frames * params.sampwidth * params.nchannels)
            for i, w in enumerate(wavs):
                with wave.open(w, "rb") as r:
                    out.writeframes(r.readframes(r.getnframes()))
                if i < len(wavs) - 1 and silence:
                    out.writeframes(silence)
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", merged, "-ar", "24000", "-ac", "1",
             "-c:a", "libmp3lame", "-b:a", "48k", "-write_id3v1", "0", "-id3v2_version", "0", out_mp3],
            check=True,
        )
        blob = open(out_mp3, "rb").read()
        total = duration_of(out_mp3)
        kind = f"문단 사이 {args.gap:.2f}s 무음"
    else:
        # ffmpeg 없음: 프레임 이어붙이기(무음 없음)
        blob = b"".join(frames)
        with open(out_mp3, "wb") as f:
            f.write(blob)
        total = duration_of(out_mp3)
        kind = "프레임 이어붙이기(무음 없음)"

    # 타임라인: 각 문단 시작/끝(초)
    t = 0.0
    for i, (stage, d) in enumerate(zip(stages, durations)):
        if i:
            t += args.gap
        stage["start"] = round(t, 2)
        stage["end"] = round(t + d, 2)
        t += d

    doc["voice"] = voice
    doc["audio"] = {
        "src": f"data/briefing-{doc['date']}.mp3",
        "duration": round(total, 2),
        "bytes": len(blob),
        "segments": [round(d, 2) for d in durations],
        "format": f"mp3 · mono · {kind}",
        "rate": args.rate,
        "pitch": args.pitch,
    }

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\nvoice={voice}  rate={args.rate}  pitch={args.pitch}  문단 {len(stages)}개  합계 {total:.2f}s")
    print(f"파일 {out_mp3} ({len(blob)/1024:.0f} KB)")
    if abs(total - (sum(durations) + args.gap * (len(durations) - 1))) > 0.75:
        print("⚠ 합본 길이가 예상과 어긋납니다. 타임라인을 확인하세요.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
