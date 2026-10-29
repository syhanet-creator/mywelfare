"""나의 복지 찾기: 행정안전부_대한민국 공공서비스(혜택) 정보 오픈API(공공데이터포털 15113968)를 검색한다.

호출하는 주소(odcloud, 인증키 필요 - 환경변수 DATA_GO_KR_KEY):
  GET /api/gov24/v3/serviceList        : 서비스명·지원대상·지원내용·신청방법·소관기관 등
  GET /api/gov24/v3/supportConditions  : 서비스별 지원 조건(성별 JA0101/02, 대상 연령 JA0110~11, 소득구간 JA0201~05)
서비스 목록은 수천 건이라 한 번 내려받아 메모리에 6시간 보관하고, 검색·거름은 서버에서 한다.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    from . import _eligibility as elig
except ImportError:  # Vercel(api/ 가 경로에 있는 경우)
    import _eligibility as elig

BASE = "https://api.odcloud.kr/api/gov24/v3"
PER_PAGE = 1000
TTL = 6 * 3600
PAGE_SIZE = 20
MAX_PAGES = 30  # 안전장치(3만 건)
_cache = {"at": 0, "list": [], "cond": {}, "cond_ok": False}

# 소득 구간(기준 중위소득 %) -> supportConditions 코드
INCOME_CODES = {"0-50": "JA0201", "50-75": "JA0202", "75-100": "JA0203", "100-200": "JA0204", "200+": "JA0205"}
# 내 상황 -> 지원대상·서비스명에서 찾을 말
SITUATIONS = {
    "청년": ["청년", "대학생", "사회초년"],
    "중장년": ["중장년", "신중년", "장년"],
    "노인": ["노인", "어르신", "고령", "65세"],
    "임산부·출산": ["임산부", "임신", "출산", "산모", "신생아", "난임"],
    "영유아·아동": ["영유아", "아동", "어린이", "보육", "육아"],
    "청소년": ["청소년", "학교밖"],
    "한부모·조손": ["한부모", "조손", "미혼모"],
    "장애인": ["장애"],
    "저소득": ["저소득", "기초생활", "차상위", "수급자", "취약계층"],
    "다문화·외국인": ["다문화", "외국인", "이주"],
    "국가보훈": ["보훈", "유공자", "참전"],
    "농어업인": ["농업", "어업", "농민", "어민", "귀농", "귀촌", "임업"],
    "소상공인·창업": ["소상공인", "창업", "자영업"],
    "구직·실직": ["구직", "실업", "실직", "취업", "고용"],
    "주거": ["주거", "임대", "전세", "월세", "주택"],
    "질병·의료": ["질병", "의료", "치료", "암", "희귀"],
    "신혼부부": ["신혼", "결혼", "혼인"],
    "다자녀": ["다자녀", "셋째", "다둥이"],
    "재직자": ["재직", "근로자", "직장인", "중소기업"],
    "학생": ["학생", "장학", "대학", "학자금"],
}


def _key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    return urllib.parse.quote(urllib.parse.unquote(k), safe="") if k else ""  # 인코딩/디코딩 키 모두 허용


def _get(endpoint, page):
    url = f"{BASE}/{endpoint}?page={page}&perPage={PER_PAGE}&returnType=JSON&serviceKey={_key()}"
    req = urllib.request.Request(url, headers={"User-Agent": "saeul-welfare", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fetch_all(endpoint):
    first = _get(endpoint, 1)
    total = int(first.get("totalCount") or 0)
    rows = list(first.get("data") or [])
    pages = min(MAX_PAGES, -(-total // PER_PAGE))
    if pages > 1:
        with ThreadPoolExecutor(6) as ex:
            for d in ex.map(lambda p: _get(endpoint, p), range(2, pages + 1)):
                rows.extend(d.get("data") or [])
    return rows


def _load():
    if _cache["list"] and time.time() - _cache["at"] < TTL:
        return
    services = _fetch_all("serviceList")
    for s in services:
        _analyze(s)
    cond, cond_ok = {}, False
    try:
        for c in _fetch_all("supportConditions"):
            cond[c.get("서비스ID")] = c
        cond_ok = True
    except Exception:  # 조건 정보가 없어도 키워드 검색은 동작한다
        pass
    _cache.update(at=time.time(), list=services, cond=cond, cond_ok=cond_ok)


# 2026년 기준 중위소득(원/월), 가구원 수별. 7인 초과는 1인 증가마다 +959,199
MEDIAN = {1: 2564238, 2: 4199292, 3: 5359036, 4: 6494738, 5: 7556719, 6: 8555952, 7: 9515151}
_AMT = re.compile(r"소득((?:(?!재산|가액|보증금|주택|매출|자산|○|ㆍ|※)[^\n]){0,60}?)(\d[\d,]*(?:\.\d+)?)\s*(천만|억|만)\s*원\s*(이하|미만)")
_BARE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(천만|억|만)\s*원\s*(?:이하|미만)")
_UNIT = {"천만": 1000, "억": 10000, "만": 1}
INCOME_LOWER = {"0-50": 0, "50-75": 50, "75-100": 75, "100-200": 100, "200+": 200}
_CAP = re.compile(r"중위\s*소득\s*(?:의\s*)?(\d{2,3})\s*%\s*(?:이하|미만)")
_LOW = re.compile(r"기초생활|수급자|수급권자|차상위|저소득|생활이\s*어려운|소득과\s*재산이\s*적은|소득이\s*낮은|위기가구|긴급복지|의료급여|한부모가족지원")
_OPEN = re.compile(r"전\s*국민|누구나|소득\s*(?:기준\s*)?(?:무관|제한\s*없)|소득에\s*관계\s*없")
_NOUN = r"(?:청년|어린이|청소년|아동|영유아|학생|어르신|노인|신청인|세대주|구직자|근로자|주민|시민|농업인|사람|이상|이하|자(?!녀))"
_RANGE = re.compile(r"(?:만\s*)?(\d{1,2})\s*세?\s*(?:이상)?\s*[~∼\-]\s*(?:만\s*)?(\d{1,2})\s*세\s*" + _NOUN)
_MIN = re.compile(r"만\s*(\d{2})\s*세\s*이상\s*(?:의\s*)?(?:노인|어르신|주민|국민|신청인|시민|여성|남성|농업인|근로자|구직자)(?!\s*제외)")
_MAX = re.compile(r"만\s*(\d{1,2})\s*세\s*(?:이하|미만)\s*(?:의\s*)?(?:어린이|청소년|아동|영유아)")


def _analyze(s):
    """본문에서 소득 상한(중위소득 N% 이하)·저소득 대상 여부·나이 조건을 뽑는다(판정은 참고용)."""
    txt = " ".join(str(s.get(k, "")) for k in ("서비스명", "서비스목적요약", "지원대상", "선정기준"))
    caps = [int(x) for x in _CAP.findall(txt)]
    head = " ".join(str(s.get(k, "")) for k in ("서비스명", "서비스목적요약", "지원대상"))
    s["_cap"] = max(caps) if caps else None
    s["_low"] = bool(_LOW.search(head)) and not _OPEN.search(head)
    ages = [(int(a), int(b)) for a, b in _RANGE.findall(txt)]
    ages += [(int(a), 200) for a in _MIN.findall(txt)]
    ages += [(0, int(a)) for a in _MAX.findall(txt)]
    s["_ages"] = ages
    caps_amt = []  # 연 소득 상한(만원). '월 소득 N만원 이하'는 ×12
    for m in _AMT.finditer(txt):
        v = float(m.group(2).replace(",", "")) * _UNIT[m.group(3)]
        if "월" in txt[max(0, m.start() - 4):m.start() + 3] or "월" in m.group(1):
            v *= 12
        caps_amt.append(v)
        tail = txt[m.end():m.end() + 80]  # 같은 문장의 다른 유형(신혼가구 N천만원 이하 등)도 상한 후보
        for m2 in _BARE.finditer(tail):
            if not re.search(r"재산|가액|보증금|주택|매출|자산|○|ㆍ|※", tail[:m2.start()]):
                caps_amt.append(float(m2.group(1).replace(",", "")) * _UNIT[m2.group(2)])
    s["_scap"] = max(caps_amt) if caps_amt else None


def _text_fit(s, age, income, include_low, salary=None):
    lower = INCOME_LOWER.get(income)
    if lower is not None and not include_low:
        cap = s.get("_cap")
        if cap is not None and cap <= lower:
            return False
        if lower >= 75 and s.get("_low") and (cap is None or cap <= lower):
            return False
    if salary and s.get("_scap") and s["_scap"] < salary:
        return False
    ages = s.get("_ages")
    if age is not None and ages and all(not (lo <= age <= hi) for lo, hi in ages):
        return False
    return True


def _num(v):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _fit(c, age, sex, income):
    """지원 조건이 내 정보와 맞지 않는 것이 확실하면 False. 조건이 비어 있으면 통과."""
    if not c:
        return True
    lo, hi = _num(c.get("JA0110")), _num(c.get("JA0111"))
    if age is not None and (lo or hi):
        if (lo and age < lo) or (hi and age > hi):
            return False
    if sex in ("M", "F"):
        m, f = c.get("JA0101") == "Y", c.get("JA0102") == "Y"
        if (m or f) and not (m and f) and ((sex == "M") != m):
            return False
    if income in INCOME_CODES:
        flags = [c.get(code) == "Y" for code in INCOME_CODES.values()]
        if any(flags) and c.get(INCOME_CODES[income]) != "Y":
            return False
    return True


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def elig_lower(income):
    return INCOME_LOWER.get(income)


def search(q="", age=None, sex="", income="", situations=(), field="", user_type="", sort="rel", page=1, include_low=False, salary=None, hh=1, sido="", sigungu="", view="ok"):
    if not _key():
        return {"error": "no_key", "message": "서버에 DATA_GO_KR_KEY(공공데이터포털 인증키)가 설정되어 있지 않습니다."}
    try:
        _load()
    except Exception as e:
        return {"error": "upstream", "message": f"공공데이터포털 호출 실패: {type(e).__name__}"}

    band = None
    if salary:  # 가구 합산 세전 연봉(만원) -> 기준 중위소득 대비 %와 구간
        med = MEDIAN.get(hh) or MEDIAN[7] + 959199 * (hh - 7)
        pct = salary * 10000 / 12 / med * 100
        income = next(k for k, hi in (("0-50", 50), ("50-75", 75), ("75-100", 100), ("100-200", 200), ("200+", 1e9)) if pct <= hi)
        band = {"pct": round(pct, 1), "band": income, "median": med}
    terms = [t for t in re.split(r"[\s,]+", q.strip()) if t][:6]
    sit_terms = [w for s in situations if s in SITUATIONS for w in SITUATIONS[s]]
    cond = _cache["cond"]
    edata = elig.load()
    lo_pct = elig_lower(income)
    profile = {"age": age, "sex": sex if sex in ("M", "F") else "", "salary": salary,
               "pct": band["pct"] if band else None, "pct_lo": None if band else lo_pct,
               "sido": sido if sido in elig.SIDO else "", "sigungu": sigungu[:20], "include_low": include_low,
               "tags": {t for c in situations for t in elig.CHIP_TAGS.get(c, [])}}
    out, counts = [], {"ok": 0, "check": 0}
    for s in _cache["list"]:
        if field and field not in str(s.get("서비스분야", "")):
            continue
        if user_type and user_type not in str(s.get("사용자구분", "")):
            continue
        e = edata.get(s.get("서비스ID"))
        if e is not None:  # AI 로 구조화한 자격조건이 있으면 그것으로 정확히 판정
            status, notes = elig.judge(e, profile)
            if status == "no":
                continue
        else:  # 아직 분석 전: 키워드·조건코드 기준(대략)
            if not _fit(cond.get(s.get("서비스ID")), age, sex, income) or not _text_fit(s, age, income, include_low, salary):
                continue
            status, notes = "ok", []
        name = str(s.get("서비스명", ""))
        head = str(s.get("서비스목적요약", "")) + " " + str(s.get("지원대상", ""))
        body = str(s.get("지원내용", "")) + " " + str(s.get("선정기준", ""))
        score = 0
        for t in terms:
            hit = (5 if t in name else 0) + (2 if t in head else 0) + (1 if t in body else 0)
            if not hit:
                score = -1  # 검색어는 모두 들어 있어야 한다
                break
            score += hit
        if score < 0:
            continue
        if sit_terms:
            sit = sum((3 if w in name else 0) + (2 if w in head else 0) for w in sit_terms)
            if not sit and not terms and e is None:
                continue  # 분석 전 서비스는 키워드로만 판단할 수 있으므로 상황과 관련된 것만
            score += sit
        counts[status] += 1
        if status != view:
            continue
        out.append((score, _num(s.get("조회수")) or 0, s, notes, e is not None))
    out.sort(key=lambda x: (-x[0], -x[1]) if sort == "rel" else (-x[1], -x[0]))

    total = len(out)
    page = max(1, page)
    items = []
    for score, views, s, notes, analyzed in out[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]:
        items.append({
            "id": s.get("서비스ID"), "name": s.get("서비스명"), "summary": _clip(s.get("서비스목적요약"), 160),
            "target": _clip(s.get("지원대상"), 220), "content": _clip(s.get("지원내용"), 260),
            "criteria": _clip(s.get("선정기준"), 200), "how": _clip(s.get("신청방법"), 80),
            "deadline": _clip(s.get("신청기한"), 80), "org": s.get("소관기관명"), "tel": s.get("전화문의"),
            "field": s.get("서비스분야"), "type": s.get("지원유형"), "user": s.get("사용자구분"),
            "url": s.get("상세조회URL") or f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{s.get('서비스ID')}",
            "views": views, "notes": notes, "analyzed": analyzed,
        })
    fields = sorted({f.strip() for s in _cache["list"] for f in str(s.get("서비스분야", "")).split(",") if f.strip()})
    return {"total": total, "counts": counts, "analyzed": len(edata), "page": page, "pageSize": PAGE_SIZE, "items": items, "fields": fields,
            "situations": list(SITUATIONS), "sidos": elig.SIDO, "conditions": _cache["cond_ok"], "band": band,
            "updated": time.strftime("%Y-%m-%d %H:%M", time.gmtime(_cache["at"] + 9 * 3600))}


def from_query(path):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    g = lambda k: q.get(k, [""])[0].strip()
    return search(
        q=g("q")[:60], age=_num(g("age")), sex=g("sex")[:1].upper(), income=g("income")[:8],
        situations=[x for x in g("sit").split(",") if x], field=g("field")[:20], user_type=g("user")[:10],
        sort="views" if g("sort") == "views" else "rel", page=_num(g("page")) or 1, include_low=g("low") == "1",
        salary=_num(g("salary")), hh=min(max(_num(g("hh")) or 1, 1), 15),
        sido=g("sido")[:12], sigungu=g("sigungu"), view="check" if g("view") == "check" else "ok")
