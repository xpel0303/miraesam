"""문법뱅크 분류 체계: 학년 단계 → 중분류 → 소분류 (taxonomy.txt).

문항의 cat 은 "학년 단계 · 중분류"(예: "중등발전 · 시제"), subcat 은 소분류 이름, sub_id 는 소분류 번호(G5-B-03)."""
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("taxonomy.txt")


@lru_cache
def load() -> dict:
    """{"tiers": [(id, label)], "subs": {sub_id: {tier, tier_label, cat, label, idx}}}"""
    tiers, subs = [], {}
    tier = tl = cat = None
    for line in PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cat = line[3:].strip()
        elif line.startswith("# "):
            tier, tl = line[2:].split(" ", 1)
            tiers.append((tier, tl))
        elif "\t" in line:
            sid, label = line.split("\t", 1)
            subs[sid] = {"tier": tier, "tier_label": tl, "cat": cat, "label": label.strip(), "idx": len(subs)}
    return {"tiers": tiers, "subs": subs}


def cat_name(sub_id: str) -> str:
    s = load()["subs"][sub_id]
    return f"{s['tier_label']} · {s['cat']}"


def listing() -> str:
    """사람이 읽는 목록 (매핑 만들 때 참고용)."""
    out = []
    for sid, s in load()["subs"].items():
        out.append(f"{sid}\t{s['tier_label']} · {s['cat']} · {s['label']}")
    return "\n".join(out)
