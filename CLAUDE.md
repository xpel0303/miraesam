# 미래샘 영어연구소 사이트 — 작업 안내 (Claude Code용)

학원 통합 사이트(수업 자료 만들기·기출 분석·학생 리포트·숙제·문법뱅크). 기능별 설명은 README.md.
사용자(미래샘 원장님)와는 **한국어**로, 쉬운 말로 이야기합니다. 결과는 바로 쓸 수 있게.

## 어디에 있나
- 코드: GitHub `xpel0303/miraesam` (main 브랜치에 바로 올림)
- 서버: Render 웹 서비스 `miraesam` (https://miraesam.onrender.com, 무료 요금제 512MB, 싱가포르)
  - 자동 배포가 아니라 **대시보드 → Manual Deploy → Deploy latest commit** 으로 올림
- DB: Neon Postgres (주소는 Render 환경변수 `DATABASE_URL`에만 있음)

## 꼭 지킬 것
- 비밀번호·DB 주소·API 키는 Claude가 입력하거나 저장하지 않음. 필요하면 원장님이 직접 로그인/붙여넣기.
- Render 환경변수는 건드리지 않음.
- 교재·시험지 원문(저작권 있는 지문/문항)은 공개 저장소에 올리지 않음. 문법뱅크 문항은 DB에만.
- 화면·PDF 어디에도 **ARA 로고/이름을 넣지 않음** (미래샘 로고만).
- 배포하면 서버가 다시 켜지면서 돌던 작업이 처음부터 재시도됨 (최대 3번, `jobs.MAX_ATTEMPTS`). 긴 작업이 돌 때는 배포를 미룸.
- 커밋 메시지 끝에 공동 작성 표시 줄을 붙임.

## 구조
- FastAPI + Jinja2 + SQLAlchemy(Postgres), PDF는 WeasyPrint(별도 프로세스, `app/pdf.py`), 워드는 `app/docx_builders.py`.
- 오래 걸리는 일은 작업 대기열: `app/jobs.py` (`@job_handler("이름")`, `enqueue(...)`, `ctx.step(글, 진행률)`, `ctx.gemini()`), 처리기는 `app/handlers/*.py`.
- AI: Gemini, `app/llm.py` `generate(key, prompt, system=, model=, json_mode=True)`. 선생님별 키.
- 새 컬럼은 `app/db.py` 의 `ADDED_COLUMNS` 에 추가(시작할 때 자동 ALTER). 새 테이블은 `create_all`.
- 메뉴/기능 목록: `app/features.py`. 라우트: `app/routes/*.py`, 화면: `app/templates/<기능>/`, PDF: `app/templates/pdf/`.

## 로컬에서 돌려 보기
- Postgres 하나 띄우고 `DATABASE_URL=postgresql://... FAKE_LLM=1 uvicorn app.main:app --port 8766`
- `FAKE_LLM=1` 이면 `app/devfake.py` 가 시스템 프롬프트 글귀로 골라 가짜 답을 돌려줌 (새 AI 기능엔 가짜 답도 추가).
- 스크립트에서 `render_pdf` 를 부르면 `if __name__ == "__main__":` 안에서 (spawn 때문).
- 고친 뒤 최소 확인: `python3 -c "import app.main"`.

## 메모리 주의 (Render 512MB)
- 흐름분석 PDF: 감자꽃 글꼴은 KS X 1001 2350자로 줄인 것(`static/fonts/gamja-korean.woff2`), 크기 맞추기는 그림 대신 같은 크기 SVG 자리표시, 렌더는 조각(chunk)으로.

## 기능별 메모
- **단어장** (`app/vocab`): 실전형 시험지(객관식+주관식, 정답지 따로), 지문별 시험지/페이지, 단어 수 제한 없음(MAX_WORDS 3000).
- **흐름분석** (`app/flow`): 왼쪽 한글 분류·구조 도식 + 오른쪽 4컷 만화, A4 가로 한 지문 한 쪽, 감자꽃 글꼴, `**하이라이트**`.
- **단어 숙제 (클래스카드식)**: 숙제 만들 때 단어 퀴즈에 학습 단계 선택(`homework/spec.py` `STAGE_SETS`). 학생은 암기(카드)·리콜(뜻 고르기)·스펠(철자)·매칭 게임·테스트를 순서 없이. 진행은 `homework_results.stages`, 화면 `student/hw_vocab*.html`, 같은 숙제 친구들 순위(게임 기록·테스트 점수).
- **홈 화면 앱(PWA)**: `routes/pwa.py`(manifest·sw.js), `templates/_pwa_head.html`·`_pwa_install.html`, 아이콘 `static/icons/`. 서비스 워커는 저장 안 함(늘 서버에서) → 배포하면 앱도 바로 바뀜. 학생 화면에서 설치하면 '미래샘 숙제'(그 학생 코드로 시작), 선생님 화면에서 설치하면 '미래샘 교사' — 한 폰에 둘 다 깔 수 있음.
- **숙제 푸시 알림**: `app/push.py` (pywebpush). VAPID 키는 처음 쓸 때 만들어 DB `app_secrets`에 저장(환경변수 X). 기기는 `push_subs`. 숙제 내면 반 학생에게, 마감 전날 17~22시에 안 한 학생에게 1번(`options.reminded`), 숙제 화면 '안 한 학생에게 알림 보내기'. 학생 홈 '🔔 숙제 알림' 카드. 아이폰은 홈 화면 앱에서만.
- **메뉴(학원 관리)**: 학생 명단 → 학습관리(숙제·시험 결과(/reports, 옛 '학생·반 리포트')는 학습관리 안의 탭, `care/_tabs.html`) → 레벨테스트. 숙제 화면도 메뉴는 학습관리로 표시.
- **숙제 학생 고르기**: 반 안에서 일부만 → `homework.options.students`(비면 반 전체), 판단은 `homework/spec.py` `for_student`. 학생 화면·교사 표·레벨테스트·푸시 모두 이걸로 거름. 고르기 화면 `homework/_pick_students.html`.
- **설명회 자료** (`app/briefing/spec.py`·`pptx_build.py`, `routes/briefing.py`, `handlers/briefing.py`, 표 `briefings`): 종류 exam/academy/season, 데이터(`spec.facts`) + 메모 → Gemini(SYSTEM '설명회 슬라이드', devfake 있음) → slides JSON. 그래프·표 숫자는 `data` 키로 실제 데이터에서 채움(`clean_slide`). 화면·발표·PDF는 같은 `templates/briefing/_slides.html`(1280×720), PDF 틀 `pdf/briefing.html`, PowerPoint는 python-pptx(requirements 추가). 오른쪽 위 로고 = 학원 로고(`academies.logo`) 있으면 그것, 없으면 미래샘(`logo-word.png`) — 슬라이드 `brand()`, pptx `logo_png`/`_brand`, 학부모 화면·리포트도 같음.
- **교과 연계 찾기** (`app/curriculum/`): 원장님이 준 2022 개정 사회·과학 단원 정리 `data.md`(63단원)를 읽어 Gemini가 지문과 가까운 단원 1~3개 고름(`/api/curriculum`, SYSTEM '교과 연계 분석가', devfake 있음). 지문 칸 위 '📚 교과 연계 찾기' 버튼(app.js) — 누를 때만 돎, '제목에 [통합과학 2 Ⅱ] 붙이기'.
- **기출 배점**: 학교 시험은 늘 100점 만점. 못 읽은 배점은 `exam/spec.py` `fill_points` 가 (100 − 읽은 배점) ÷ 빈 문항 수로 채움(points_est). 읽기 끝날 때 저장 + 분석지·경향분석·설명회·기출 화면 통계에서도 적용.
- **10단계 본문 워크북**: 유형별/지문별 순서 선택, 지문마다 새 쪽.
- **학습관리** (`routes/care.py`, `routes/parent.py`, `app/care.py`, `app/push.py`): 리포트닷(reportdot.net)을 참고한 매일 기록 표 + 학부모 PWA. 표: `care_class_days`(반 하루), `care_records`(학생 하루, 반 값보다 우선), `care_notices`, `parent_push_subs`(학부모 폰, 학생 숙제 알림 `push_subs`와 따로), `push_logs`, VAPID 키는 숙제 알림과 같은 `app_secrets`. 학부모 링크 `/p/{students.parent_token}`, 서비스워커 `/p/sw.js`(static/parent-sw.js). 알림은 스레드로 바로 보냄(작업 대기열 아님). 2단계(`routes/care_more.py`): 교재 `care_books`·`care_book_uses`(목차 units, 반/학생 연결), 매일 기록 진도 `prog_items`, 사진 `care_photos`·`care_photo_students`(DB에 JPEG, QR 업로드 `care_upload_tokens` /up/), 월간 리포트 `care_reports`(숫자는 `care.report_data` 로 매번 계산, 학부모 /p/{token}/r/{id}). AI: 목차 사진 → 단원(TOC_SYSTEM), 리포트 코멘트 초안(REPORT_SYSTEM), devfake 에 가짜 답 있음. 아직 없는 것: 선생님별 담당 반 권한, 주간·일간 리포트 따로(기간은 자유롭게 고를 수 있음). 사진이 많아지면 Neon 무료 용량(0.5GB)을 봐야 함.
- **문법뱅크** (`app/bank`):
  - 분류는 지인에듀(ginedu.com/grammar_bank) 체계: 학년 단계 7개(g1 초등기초 ~ g7 고등) → 중분류 91 → 소분류 707, `bank/taxonomy.txt`.
  - 문항 `cat` = "학년 단계 · 중분류", `subcat` = 소분류, `tier`, `sub_id`. 원래 분류는 `old_cat`/`old_sub`.
  - 재배치 규칙: `bank/rules.py` (교재 학년 단계 + 기존 분류, 동점이면 높은 단계).
  - 문제 만들기 화면 `/bank/build`: 지인에듀 배치(학년 단계 탭 → 중분류 트리 → 소분류 체크, 개념·분량·문항 조건·세트 수, PDF 출력 설정 탭).
  - 개념 설명 카드: Gemini로 소분류마다 만들어 `bank_concepts` 테이블에 저장, PDF 앞에 들어감.
  - **AI 문항 채우기** (`handlers/bank.py` `bank_fill`, 가져오기 화면 버튼): 빈 중분류(중분류당 200) / 빈 소분류(소분류당 N) / 멈춘 작업 이어받기(resume). 문제 만든 뒤 AI가 다시 풀어 보고 안 맞으면 버림. 가져오기 화면 아래 'AI 문항 채우기 작업' 표에 최근 작업 상태·채운 수가 나오고, 덜 찬 작업엔 '이어서 채우기' 버튼(같은 작업이 돌고 있으면 막힘). `book="AI 생성 문항"`, `src="AI 생성"`, 밑줄은 〔 〕.

## 남은 일
- **원장님 요청(11/18): 문법뱅크 그만 만들기.** AI 문항 채우기는 `handlers/bank.py` `FILL_PAUSED = True` 로 꺼 둠(버튼 숨김, 대기·재시작 작업도 바로 끝남). 이미 만든 문항·문제 만들기 화면은 그대로. 아래 문법뱅크 항목들은 원장님이 다시 하자고 할 때까지 보류.
- **원장님 요청(11/13 밤): 모든 소분류를 100문항씩.** 가져오기 화면 '소분류마다 문항 수 맞추기'(min_total=100) — 교재 문항 + AI 문항이 100이 되도록 모자란 만큼만 AI로 만듦. 배포 후, 다른 채우기 작업이 끝난 다음에 누를 것 (수만 문항, 여러 시간).
- 2026-11-13 시작한 채우기 작업(작업 69: 빈 소분류 20씩, 작업 70: 68번 이어서) 결과 확인 — 가져오기 화면의 작업 표에서 '이어서 채우기'.
- 남은 빈 소분류 확인 (`/bank/build` 에서 0인 소분류).
- 문법뱅크 정답 점검 (원장님이 분류 재배치 뒤로 미룸) — 학년 단계별로.
- AI 생성 문항 검수.

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
