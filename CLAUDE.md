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
- **10단계 본문 워크북**: 유형별/지문별 순서 선택, 지문마다 새 쪽.
- **문법뱅크** (`app/bank`):
  - 분류는 지인에듀(ginedu.com/grammar_bank) 체계: 학년 단계 7개(g1 초등기초 ~ g7 고등) → 중분류 91 → 소분류 707, `bank/taxonomy.txt`.
  - 문항 `cat` = "학년 단계 · 중분류", `subcat` = 소분류, `tier`, `sub_id`. 원래 분류는 `old_cat`/`old_sub`.
  - 재배치 규칙: `bank/rules.py` (교재 학년 단계 + 기존 분류, 동점이면 높은 단계).
  - 문제 만들기 화면 `/bank/build`: 지인에듀 배치(학년 단계 탭 → 중분류 트리 → 소분류 체크, 개념·분량·문항 조건·세트 수, PDF 출력 설정 탭).
  - 개념 설명 카드: Gemini로 소분류마다 만들어 `bank_concepts` 테이블에 저장, PDF 앞에 들어감.
  - **AI 문항 채우기** (`handlers/bank.py` `bank_fill`, 가져오기 화면 버튼): 빈 중분류(중분류당 200) / 빈 소분류(소분류당 N) / 멈춘 작업 이어받기(resume). 문제 만든 뒤 AI가 다시 풀어 보고 안 맞으면 버림. `book="AI 생성 문항"`, `src="AI 생성"`, 밑줄은 〔 〕.

## 남은 일
- 2026-11-13 시작한 채우기 작업(작업 69: 빈 소분류 20씩, 작업 70: 68번 이어서) 결과 확인 — 실패했으면 가져오기 화면에서 resume.
- 남은 빈 소분류 확인 (`/bank/build` 에서 0인 소분류).
- 문법뱅크 정답 점검 (원장님이 분류 재배치 뒤로 미룸) — 학년 단계별로.
- AI 생성 문항 검수.
