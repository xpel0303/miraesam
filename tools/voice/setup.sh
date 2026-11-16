#!/usr/bin/env bash
# Chatterbox(다국어) 음성 복제 환경을 설치한다. 처음 한 번만 실행하면 된다(약 2~3분).
# 사용법: bash tools/voice/setup.sh [venv 경로]
set -euo pipefail
VENV="${1:-${VOICE_VENV:-$HOME/.cache/mirae-voice-venv}}"

command -v uv >/dev/null 2>&1 || pip install -q uv
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg가 필요합니다" >&2; exit 1; }

if [ ! -x "$VENV/bin/python" ]; then
  uv venv -q --python 3.11 "$VENV"
fi
# setuptools<81: watermark 패키지(perth)가 pkg_resources 를 쓴다
uv pip install -q --python "$VENV/bin/python" chatterbox-tts "setuptools<81"
"$VENV/bin/python" -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS; print('ok')"
echo "VENV=$VENV"
