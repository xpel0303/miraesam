"""mirae 목소리로 원고를 읽어 오디오 파일을 만든다 (Chatterbox 다국어, MIT 라이선스).

사용법:
  python speak.py --ref mirae_voice_ref.mp3 --out narration.mp3 --text "안녕하세요, 여러분!"
  python speak.py --ref mirae_voice_ref.mp3 --out narration.mp3 --file script.txt

- 원고는 문장 단위로 나눠 생성한 뒤 짧은 쉼을 넣어 이어 붙인다.
- 줄 맨 앞에 [en] 또는 [ko] 를 붙이면 그 줄의 언어를 지정한다. 없으면 자동 판별
  (한글이 있으면 ko, 없으면 en).
- 출력 확장자가 .mp3 면 mp3, 그 외에는 wav 로 저장한다.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

import torch
import torchaudio as ta

HANGUL = re.compile(r"[가-힣]")
SENT_SPLIT = re.compile(r"(?<=[.!?。？！])\s+")


def detect_lang(text):
    return "ko" if HANGUL.search(text) else "en"


def split_script(raw):
    """(lang, sentence) 목록을 돌려준다."""
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        lang = None
        m = re.match(r"^\[(ko|en)\]\s*", line)
        if m:
            lang = m.group(1)
            line = line[m.end():]
        for sent in SENT_SPLIT.split(line):
            sent = sent.strip()
            if sent:
                items.append((lang or detect_lang(sent), sent))
    return items


def to_ref_wav(ref, tmpdir):
    """기준 음성을 24kHz 모노, 최대 12초 wav 로 맞춘다."""
    out = os.path.join(tmpdir, "ref.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", ref, "-t", "12",
         "-ac", "1", "-ar", "24000", out],
        check=True,
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="mirae 기준 녹음 파일 (mp3/m4a/wav)")
    ap.add_argument("--out", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file")
    ap.add_argument("--pause", type=float, default=0.35, help="문장 사이 쉼(초)")
    ap.add_argument("--cfg", type=float, default=0.5)
    ap.add_argument("--exaggeration", type=float, default=0.5)
    args = ap.parse_args()

    raw = args.text if args.text is not None else open(args.file, encoding="utf-8").read()
    items = split_script(raw)
    if not items:
        sys.exit("원고가 비어 있습니다")

    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    torch.set_num_threads(os.cpu_count() or 4)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)

    with tempfile.TemporaryDirectory() as tmp:
        ref = to_ref_wav(args.ref, tmp)
        pause = torch.zeros(1, int(model.sr * args.pause))
        chunks = []
        for i, (lang, sent) in enumerate(items, 1):
            print(f"[{i}/{len(items)}] ({lang}) {sent}", flush=True)
            wav = model.generate(
                sent, language_id=lang, audio_prompt_path=ref,
                cfg_weight=args.cfg, exaggeration=args.exaggeration,
            )
            chunks += [wav.cpu(), pause]
        audio = torch.cat(chunks[:-1], dim=1)

        if args.out.lower().endswith(".mp3"):
            tmp_wav = os.path.join(tmp, "out.wav")
            ta.save(tmp_wav, audio, model.sr)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_wav,
                 "-codec:a", "libmp3lame", "-q:a", "2", args.out],
                check=True,
            )
        else:
            ta.save(args.out, audio, model.sr)
    print("saved", args.out)


if __name__ == "__main__":
    main()
