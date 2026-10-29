"""AI 가 구조화해 둔 자격조건(data/eligibility.json)으로 내 정보와 정확히 비교한다.

판정은 3단계: ok(해당) / check(확인 필요) / no(비해당).
  - 확실히 맞지 않는 것(나이·성별·지역·소득 상한)만 no
  - 조건이 별도 기준이거나 내가 해당 집단인지 알 수 없으면 check
"""
import json
import os

SIDO = ["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시", "세종특별자치시",
        "경기도", "강원특별자치도", "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도"]
# 서비스가 요구하는 집단(해당 집단 중 하나) / 요건(모두)
GROUPS = ["기초수급", "차상위", "저소득", "장애인", "국가보훈", "다문화", "한부모조손", "임신출산", "영유아자녀", "아동청소년자녀",
          "다자녀", "신혼부부", "농어업인", "자영업소상공인", "질병의료대상", "군인", "학생"]
REQUIRES = ["무주택", "재직자", "구직자", "자영업소상공인", "농어업인", "학생", "임신출산", "자녀양육", "기초수급", "차상위"]
LOW_GROUPS = {"기초수급", "차상위", "저소득"}
# 화면의 '내 상황' 칩 -> 내가 가진 태그
CHIP_TAGS = {
    "노인": [], "청년": [], "중장년": [], "청소년": ["학생"],
    "임산부·출산": ["임신출산"], "영유아·아동": ["영유아자녀", "아동청소년자녀", "자녀양육"],
    "한부모·조손": ["한부모조손", "자녀양육"], "장애인": ["장애인"], "저소득": ["저소득", "기초수급", "차상위"],
    "다문화·외국인": ["다문화"], "국가보훈": ["국가보훈"], "농어업인": ["농어업인"],
    "소상공인·창업": ["자영업소상공인"], "구직·실직": ["구직자"], "주거": ["무주택"], "질병·의료": ["질병의료대상"],
    "신혼부부": ["신혼부부"], "다자녀": ["다자녀", "자녀양육"], "재직자": ["재직자"], "학생": ["학생"],
}
LABEL = {"기초수급": "기초생활수급자", "차상위": "차상위계층", "저소득": "저소득층", "한부모조손": "한부모·조손가정",
         "임신출산": "임신·출산", "영유아자녀": "영유아 자녀", "아동청소년자녀": "아동·청소년 자녀", "자영업소상공인": "소상공인·자영업자",
         "질병의료대상": "질병·의료 대상", "자녀양육": "자녀 양육", "농어업인": "농·어업인"}

_data = None


def load(path=None):
    global _data
    if _data is None:
        path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "eligibility.json")
        try:
            with open(path, encoding="utf-8") as f:
                _data = json.load(f).get("items", {})
        except (OSError, ValueError):
            _data = {}
    return _data


def _lab(t):
    return LABEL.get(t, t)


def _same_place(a, b):
    a, b = a.replace(" ", ""), b.replace(" ", "")
    return bool(a and b and (a in b or b in a))


def judge(e, p):
    """e: 서비스 자격조건, p: 내 정보 {age, sex, salary(만원), pct, pct_lo, sido, sigungu, tags(set), include_low}
    -> (status, notes)"""
    notes = []
    check = False

    a = p.get("age")
    if a is not None:
        lo, hi = e.get("age_min"), e.get("age_max")
        if (lo is not None and a < lo) or (hi is not None and a > hi):
            return "no", [f"나이 {lo if lo is not None else ''}~{hi if hi is not None else ''}세 대상"]

    if p.get("sex") and e.get("sex") and p["sex"] != e["sex"]:
        return "no", ["성별 제한"]

    sido = e.get("sido") or []
    if sido:
        if p.get("sido") and p["sido"] not in sido:
            return "no", [f"{', '.join(sido)} 거주자 대상"]
        sg = e.get("sigungu") or []
        if sg:
            if p.get("sigungu"):
                if not any(_same_place(p["sigungu"], x) for x in sg):
                    return "no", [f"{', '.join(sg)} 거주자 대상"]
            else:
                check = True
                notes.append(f"{', '.join(sg)} 거주자인지 확인")
        elif not p.get("sido"):
            check = True
            notes.append(f"{', '.join(sido)} 거주자 대상")

    cap = e.get("income_pct_max")
    if cap is not None:
        if p.get("pct") is not None and p["pct"] > cap:
            return "no", [f"기준 중위소득 {cap}% 이하 대상(내 소득 약 {round(p['pct'])}%)"]
        if p.get("pct") is None and p.get("pct_lo") is not None and p["pct_lo"] >= cap:
            return "no", [f"기준 중위소득 {cap}% 이하 대상"]
    ann = e.get("income_annual_max_man")
    if ann and p.get("salary") and p["salary"] > ann:
        if e.get("income_basis") == "individual":
            check = True
            notes.append(f"본인 연소득 {ann:,}만원 이하 대상(입력은 가구 합산)")
        else:
            return "no", [f"연소득 {ann:,}만원 이하 대상(내 연봉 {p['salary']:,}만원)"]
    if e.get("income_other"):
        check = True
        notes.append("소득 기준이 별도(건강보험료·소득인정액 등)")

    mine = p.get("tags") or set()
    groups = e.get("groups") or []
    if groups and not (set(groups) & mine):
        high = p.get("pct_lo") is not None and p["pct_lo"] >= 100 or (p.get("pct") or 0) >= 100
        if high and set(groups) <= LOW_GROUPS and not p.get("include_low"):
            return "no", [f"{'·'.join(_lab(g) for g in groups)} 대상"]
        check = True
        notes.append("대상: " + "·".join(_lab(g) for g in groups))
    need = [r for r in (e.get("requires") or []) if r not in mine and r not in groups]
    if need:
        check = True
        notes.append("요건: " + "·".join(_lab(r) for r in need))
    if e.get("confidence") == "low":
        check = True
        notes.append("조건 해석이 불확실")
    return ("check" if check else "ok"), notes
