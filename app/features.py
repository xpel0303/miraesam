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
    Feature("analysis", "지문 분석", "수업 자료", "문장별 구문 분석·해석·어휘·어법 포인트", 2, True, "/analysis"),
    Feature("comprehension", "내용이해 워크북", "수업 자료", "OX 한글·영어, 목표어법 작문, 단어 뜻 쓰기", 2, True, "/comprehension"),
    Feature("variant", "변형문제", "수업 자료", "빈칸·순서·삽입·요약 등 수능 유형 변형문제", 2, True, "/variant"),
    Feature("sentence", "문장 시험지", "수업 자료", "해석·영작·배열·빈칸 문장 단위 시험지", 2, True, "/sentence"),
    Feature("vocab", "단어장", "수업 자료", "지문 단어장, 유의어·반의어, 단어 시험, 학생 퀴즈", 2, True, "/vocab"),
    Feature("grammar", "어법 문제", "수업 자료", "목표어법 문제, 밑줄 5지선다, 어법 개수", 2, True, "/grammar"),
    Feature("grammar_bank", "문법뱅크", "수업 자료", "개념 학습지와 문법 문항 은행", 2),
    Feature("paraphrase", "지문 변형", "수업 자료", "원문을 학교 시험처럼 바꿔 쓰기, 원문 대조", 2, True, "/paraphrase"),
    # 시험 운영
    Feature("exam", "기출 분석", "시험 운영", "시험지 PDF·사진 → 문항 추출, 유형·배점 분석지, 리포트 연결", 1, True, "/exam"),
    Feature("trend", "학교 경향분석", "시험 운영", "같은 학교 기출이 쌓이면 자동 생성", 1),
    Feature("reports", "학생·반 리포트", "시험 운영", "자동 채점, 학생별 오답 리포트, 반 리포트·결과 이미지", 1, True, "/reports"),
    Feature("mock", "동형 모의고사", "시험 운영", "학교 시험지 양식 그대로 새 시험지", 3),
    Feature("briefing", "설명회 자료", "시험 운영", "분석 문서를 골라 PowerPoint로", 3),
    # 학원 관리
    Feature("roster", "학생 명단", "학원 관리", "학교·반·학생 관리와 학생 접속 코드", 0, True, "/roster"),
    Feature("leveltest", "레벨테스트", "학원 관리", "반배치고사 응시와 성적 리포트", 4),
    Feature("homework", "숙제", "학원 관리", "단어·OX 퀴즈, 시험지 답 입력, 자동 채점, 카톡 문구", 4, True, "/homework"),
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
