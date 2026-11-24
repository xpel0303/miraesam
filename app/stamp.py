"""스탬프 챌린지: 학생용 스탬프 카드(A5) · 학부모 안내문(A4) · 홍보 포스터(A3) 설정.

설정은 따로 테이블 없이, 마지막으로 만든 PDF 작업(payload)에서 다시 불러옴.
"""
from datetime import date

ACCENTS = [("#F6C744", "노랑"), ("#8FD3B6", "민트"), ("#F4A6A0", "분홍"), ("#A9C4F5", "하늘")]
DOCS = [("card", "학생용 스탬프 카드 (A5)"), ("notice", "학부모 안내문 (A4)"), ("poster", "학생 홍보 포스터 (A3)")]

DEFAULTS = {
    "brand": "미래샘 영어연구소",
    "start": "2026-12-01",
    "item1": "출석", "rule1": "지각 없이 등원", "rule1_long": "수업 시작 전 지각 없이 등원",
    "item2": "숙제", "rule2": "100% 완료", "rule2_long": "과제 100% 완료",
    "item3": "테스트", "rule3": "80점 이상 통과", "rule3_long": "단어·문법 테스트 80점 이상 통과",
    "mid_gift": "키링",
    "value": "5천 원",
    "rewards": "편의점 금액권, 올리브영 상품권, 카페 음료, 아이스크림, 문화상품권",
    "bonus": "",
    "phone": "",
    "accent": ACCENTS[0][0],
}
FIELDS = list(DEFAULTS)


def clean(raw: dict) -> dict:
    s = {k: " ".join(str(raw.get(k) or "").split())[:120] for k in FIELDS}
    for k in ("brand", "start", "item1", "rule1", "item2", "rule2", "item3", "rule3", "mid_gift", "value", "rewards"):
        s[k] = s[k] or DEFAULTS[k]
    for i in "123":  # 긴 기준(안내문 표)이 비면 짧은 기준을 그대로
        s[f"rule{i}_long"] = s[f"rule{i}_long"] or s[f"rule{i}"]
    if s["accent"] not in {a for a, _ in ACCENTS}:
        s["accent"] = DEFAULTS["accent"]
    try:
        date.fromisoformat(s["start"])
    except ValueError:
        s["start"] = DEFAULTS["start"]
    return s


def view(s: dict, today: date) -> dict:
    """PDF 틀에 넘길 값 (설정 + 날짜 글귀·리워드 목록)."""
    d = date.fromisoformat(s["start"])
    return {
        **s,
        "items": [(s[f"item{i}"], s[f"rule{i}"], s[f"rule{i}_long"]) for i in "123"],
        "reward_list": [r.strip() for r in s["rewards"].replace("·", ",").split(",") if r.strip()],
        "start_dot": f"{d.year}.{d.month}.{d.day}",
        "start_md": f"{d.month}월 {d.day}일",
        "issued": f"{today.year}년 {today.month}월",
    }
