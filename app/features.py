"""사이트 전체 기능 목록 = 메뉴.

ready=True 인 기능만 실제로 열리고, 나머지는 '준비 중' 안내 화면으로 갑니다.
새 기능을 만들면 여기서 ready=True 와 path 만 바꾸면 메뉴에 반영됩니다.
stage 는 설계서 통합 로드맵의 단계 번호입니다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    name: str
    group: str
    summary: str
    stage: int
    ready: bool = False
    path: str | None = None

    @property
    def url(self) -> str:
        return self.path if (self.ready and self.path) else f"/soon/{self.key}"


GROUPS = ["수업 자료", "시험 운영", "학원 관리"]

FEATURES: list[Feature] = [
    # 수업 자료
    Feature("library", "지문 보관함", "수업 자료", "한 번 넣은 지문을 저장해 두고 어느 자료에서나 불러오기", 2, True, "/library"),
    Feature("summary", "지문 한쪽 정리", "수업 자료", "지문·해석 나란히, 제목·주제·목적·세 줄 요약, 주요 어휘, 끊어 읽기 한 줄 해석", 2, True, "/summary"),
    Feature("flow", "흐름분석", "수업 자료", "지문을 우리말로 분류·구조화한 흐름도 + 중요 단어로 그린 네 컷 만화 (한 장)", 2, True, "/flow"),
    Feature("textbook", "본문 워크북 (10단계)", "수업 자료", "본문 해석지·빈칸(우리말/영문)·해석·이야기 순서배열·어법 선택·어색한 곳·순서배열·영작·Check + 정답", 2, True, "/textbook"),
    Feature("analysis", "지문 분석", "수업 자료", "문장별 구문 분석·해석·어휘·어법 포인트", 2, True, "/analysis"),
    Feature("comprehension", "내용이해 워크북", "수업 자료", "OX 한글·영어, 목표어법 작문, 단어 뜻 쓰기", 2, True, "/comprehension"),
    Feature("variant", "변형문제", "수업 자료", "빈칸·순서·삽입·요약 등 수능 유형 변형문제", 2, True, "/variant"),
    Feature("vocab", "단어장", "수업 자료", "지문 단어장, 유의어·반의어, 단어 시험, 학생 퀴즈", 2, True, "/vocab"),
    Feature("grammar", "어법 문제", "수업 자료", "목표어법 문제, 밑줄 5지선다, 어법 개수", 2, True, "/grammar"),
    Feature("grammar_bank", "문법뱅크", "수업 자료", "대분류·세부·난이도로 골라 담아 시험지·정답지 만들기", 2, True, "/bank"),
    Feature("paraphrase", "지문 변형", "수업 자료", "원문을 학교 시험처럼 바꿔 쓰기, 원문 대조", 2, True, "/paraphrase"),
    # 시험 운영
    Feature("exam", "기출 분석", "시험 운영", "시험지 PDF·사진 → 문항 추출, 유형·배점 분석지, 리포트 연결", 1, True, "/exam"),
    Feature("trend", "학교 경향분석", "시험 운영", "같은 학교 기출을 회차별로 비교하고 출제 스타일 메모", 1, True, "/trend"),
    Feature("mock", "동형 모의고사", "시험 운영", "학교 시험지를 주면 같은 틀로 새 시험지·정답지 (새 범위 지문 또는 같은 지문)", 3, True, "/mock"),
    Feature("final", "직전대비", "시험 운영", "본문 숙지 확인 테스트 5단계 (단어→내용→핵심 표현→문장 구조→글 전체) + 정답·해설", 3, True, "/final"),
    Feature("briefing", "설명회 자료", "시험 운영", "학교별 시험대비·학원 입학·시즌 설명회 슬라이드 (AI 구성, 발표·PowerPoint·PDF)", 3, True, "/briefing"),
    # 학원 관리
    Feature("roster", "학생 명단", "학원 관리", "학교·반·학생 관리와 학생 접속 코드", 0, True, "/roster"),
    Feature("care", "학습관리", "학원 관리", "숙제 내기·자동 채점, 출결·숙제·시험·진도 매일 기록, 월간 리포트, 학부모 앱", 4, True, "/care"),
    Feature("leveltest", "레벨테스트", "학원 관리", "재원생·신규생 온라인 응시, 자동 채점, 레벨 리포트", 4, True, "/leveltest"),
]

BY_KEY = {f.key: f for f in FEATURES}


def grouped() -> list[tuple[str, list[Feature]]]:
    return [(g, [f for f in FEATURES if f.group == g]) for g in GROUPS]


STAGE_NAMES = {
    0: "0단계 · 기반 정리",
    1: "1단계 · 시험 분석",
    2: "2단계 · 수업 자료 확장",
    3: "3단계 · 실전 대비",
    4: "4단계 · 학원 관리",
}


# 선생님 권한: 기능마다 그 기능에 속한 주소. 원장이 선생님별로 고른 기능만 열림 (teachers.perms, None = 전부)
PATHS = {f.key: [f.path] for f in FEATURES if f.path}
PATHS["care"] = ["/care", "/homework", "/reports", "/up/"]


def feature_of(path: str) -> str | None:
    best, n = None, 0
    for key, ps in PATHS.items():
        for p in ps:
            if (path == p or path.startswith(p.rstrip("/") + "/")) and len(p) > n:
                best, n = key, len(p)
    return best
