# CLAUDE.md

## "mirae" 목소리

"mirae"는 이 저장소 주인(미래쌤)의 복제 목소리 이름이다. 사용자가 "mirae 목소리로"라고 하거나,
영상·음성·내레이션을 만들어 달라고 하면서 목소리를 따로 지정하지 않으면 **mirae 목소리**를 쓴다.

- 엔진: Chatterbox 다국어(MIT 라이선스, 상업적 이용 가능), CPU에서 실행. 한국어·영어 모두 지원.
- 코드: `tools/voice/setup.sh`(설치), `tools/voice/speak.py`(원고 → mp3/wav).
- **기준 녹음은 저장소에 없다**(공개 저장소라서 일부러 넣지 않음). 커밋하지 말 것.
  다음 순서로 구한다.
  1. 이번 세션에서 사용자가 첨부한 녹음 파일
  2. 사용자 Google Drive의 `mirae_voice_ref.mp3`(파일 ID `1_a91ayM8qblb3Xo6pQ3NmqNELNnkVGy9`, 비공개).
     Google Drive 커넥터의 `download_file_content`로 받으면 mp3가 tool-results 폴더에 저장되고
     그 경로가 결과에 나온다. 그 경로를 `--ref`로 쓴다. ID로 안 되면 제목으로 검색한다.
  3. 둘 다 없으면 사용자에게 녹음 파일 첨부를 요청
- Higgsfield 목소리 복제는 크레딧 부족으로 쓰지 않는다. 사용자가 따로 요청할 때만 쓴다.

### 실행 방법

```bash
bash tools/voice/setup.sh "$VENV"            # 처음 한 번, 2~3분
HF_HUB_DISABLE_XET=1 "$VENV/bin/python" tools/voice/speak.py \
  --ref <기준녹음> --file script.txt --out narration.mp3
```

- `HF_HUB_DISABLE_XET=1` 이 필요하다(이 환경에서는 Hugging Face Xet 서버가 막혀 있음).
- 원고는 문장 단위로 생성된다. 줄 앞에 `[ko]`/`[en]`을 붙이면 언어를 지정하고, 없으면 한글 유무로 자동 판별한다.
- `$VENV`는 스크래치패드 같은 임시 위치를 쓴다. 저장소 안에 만들지 않는다.
- 문장 하나에 CPU로 15~40초 걸린다. 긴 원고는 백그라운드로 돌린다.

### 영상 만들기

1. 위 방법으로 mirae 목소리 내레이션(mp3)을 만든다.
2. 영상(사용자가 준 영상, 이미지 슬라이드, 또는 생성한 영상)에 ffmpeg으로 내레이션을 입힌다.
   예: `ffmpeg -i video.mp4 -i narration.mp3 -map 0:v -map 1:a -c:v copy -shortest out.mp4`
   이미지 슬라이드라면 내레이션 길이에 맞춰 이미지를 이어 붙인다.
3. 결과물은 사용자에게 파일로 보낸다.
