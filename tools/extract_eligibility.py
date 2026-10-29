"""서비스별 자격조건(지원대상·선정기준)을 Gemini 로 구조화해 data/eligibility.json 에 저장한다.

  DATA_GO_KR_KEY=... GEMINI_API_KEY=... python tools/extract_eligibility.py [--limit N] [--max-minutes M]

- 이미 처리했고 원문이 그대로인 서비스는 건너뛴다(이어서 실행 가능, 바뀐 서비스만 다시 처리).
- 조회수 많은 서비스부터 처리한다.
- 모델은 환경변수 GEMINI_MODEL (기본 gemini-2.5-flash)
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "api"))
import _eligibility as E  # noqa: E402
import _welfare  # noqa: E402

OUT = os.path.join(ROOT, "data", "eligibility.json")
SCHEMA_VERSION = 1
BATCH = 6
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

SYSTEM = """너는 한국 공공서비스(복지 혜택)의 '신청 자격'을 구조화하는 도우미다. 서비스마다 지원대상·선정기준 원문을 읽고 아래 규칙으로 JSON 을 만든다.
원문에 근거가 있는 것만 채우고, 모르면 null/빈 배열로 둔다. 추측하지 않는다.

- age_min / age_max: 본인(신청자)의 만 나이 하한/상한. 예) '만 19~34세 청년' -> 19, 34. '만 65세 이상' -> 65, null. 자녀·피부양자 나이는 넣지 않는다(자녀 나이는 groups 의 영유아자녀/아동청소년자녀로).
  '청년'처럼 나이로 정의되는 말만 있고 숫자가 없으면 null.
- sex: 'M' 또는 'F'. 성별 제한이 있을 때만.
- income_pct_max: '기준 중위소득 N% 이하/미만'의 N. 유형이 여럿이면 가장 큰 N.
- income_annual_max_man: 연 소득 상한을 만원 단위 정수로. '연소득 6천만원 이하' -> 6000, '월 소득 500만원 이하' -> 6000(12배). 유형이 여럿이면 가장 큰 값. 재산·주택가격·보증금·매출액 상한은 소득이 아니므로 넣지 않는다.
- income_basis: 위 소득이 'household'(가구·부부합산) 인지 'individual'(본인) 인지. 모르면 null.
- income_other: 소득 조건이 있으나 위 수치로 표현되지 않으면 true (건강보험료 기준, 소득 하위 N%, 소득인정액 기준, '저소득' 등 모호한 표현 제외). 소득 조건이 없거나 수치로 표현했으면 false.
- groups: 신청자가 속해야 하는 대상 집단(해당하는 것 중 하나면 됨). 허용값: """ + ", ".join(E.GROUPS) + """.
  전 국민/일반 대상이면 빈 배열. 나이로 표현되는 청년·노인·청소년은 넣지 않는다.
- requires: 신청자가 모두 충족해야 하는 요건. 허용값: """ + ", ".join(E.REQUIRES) + """. 예) '무주택자' -> 무주택, '재직 중인 근로자' -> 재직자.
- sido: 거주(주민등록) 지역 제한이 있을 때 시·도 정식 명칭. 허용값: """ + ", ".join(E.SIDO) + """. 전국 대상이면 빈 배열. 소관기관이 지자체이고 관할 주민 대상이면 그 지자체의 시·도.
- sigungu: 시·군·구 단위 제한이 있을 때 이름(예: '수원시', '영통구'). 없으면 빈 배열.
- confidence: 원문이 명확해서 확신하면 'high', 해석이 애매하면 'low'.

입력은 JSON 배열이고, 각 항목의 id 를 그대로 돌려주는 JSON 배열로만 답한다."""


def _schema():
    s = lambda **k: {"type": "STRING", **k}
    i = {"type": "INTEGER", "nullable": True}
    arr = lambda items: {"type": "ARRAY", "items": items}
    return {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": s(), "age_min": i, "age_max": i, "sex": s(enum=["M", "F"], nullable=True),
        "income_pct_max": i, "income_annual_max_man": i,
        "income_basis": s(enum=["household", "individual"], nullable=True), "income_other": {"type": "BOOLEAN"},
        "groups": arr(s(enum=E.GROUPS)), "requires": arr(s(enum=E.REQUIRES)),
        "sido": arr(s(enum=E.SIDO)), "sigungu": arr(s()), "confidence": s(enum=["high", "low"])},
        "required": ["id", "income_other", "groups", "requires", "sido", "sigungu", "confidence"]}}


def source_text(s):
    clip = lambda k, n: " ".join(str(s.get(k) or "").split())[:n]
    return {"id": s["서비스ID"], "서비스명": clip("서비스명", 120), "소관기관": clip("소관기관명", 60),
            "기관유형": clip("소관기관유형", 20), "지원대상": clip("지원대상", 1500), "선정기준": clip("선정기준", 1500)}


def fingerprint(src):
    return hashlib.sha1((str(SCHEMA_VERSION) + json.dumps(src, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:12]


def call_gemini(batch, key, tries=6):
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(batch, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": _schema()}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    for n in range(tries):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode())
            return json.loads(d["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and n < tries - 1:
                time.sleep(min(60, 3 * 2 ** n))
                continue
            raise RuntimeError(f"Gemini HTTP {e.code}: {e.read()[:200]!r}")
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError):
            if n < tries - 1:
                time.sleep(3 * 2 ** n)
                continue
            raise


def clean(rec):
    """모델 응답을 검증하고 빈 값을 버려 작게 저장한다."""
    out = {}
    for k in ("age_min", "age_max"):
        v = rec.get(k)
        if isinstance(v, int) and 0 <= v <= 120:
            out[k] = v
    if out.get("age_min") is not None and out.get("age_max") is not None and out["age_min"] > out["age_max"]:
        out.pop("age_min"), out.pop("age_max")
    if rec.get("sex") in ("M", "F"):
        out["sex"] = rec["sex"]
    v = rec.get("income_pct_max")
    if isinstance(v, int) and 0 < v <= 1000:
        out["income_pct_max"] = v
    v = rec.get("income_annual_max_man")
    if isinstance(v, int) and 0 < v <= 10_000_000:
        out["income_annual_max_man"] = v
    if rec.get("income_basis") in ("household", "individual"):
        out["income_basis"] = rec["income_basis"]
    if rec.get("income_other") is True:
        out["income_other"] = True
    for k, allowed in (("groups", E.GROUPS), ("requires", E.REQUIRES), ("sido", E.SIDO)):
        vals = [x for x in (rec.get(k) or []) if x in allowed]
        if vals:
            out[k] = sorted(set(vals))
    sg = [str(x).strip()[:20] for x in (rec.get("sigungu") or []) if str(x).strip()]
    if sg:
        out["sigungu"] = sg[:10]
    if rec.get("confidence") == "low":
        out["confidence"] = "low"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 처리할 최대 서비스 수(0=전부)")
    ap.add_argument("--max-minutes", type=float, default=0, help="이 시간이 지나면 저장하고 종료(0=제한 없음)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or not _welfare._key():
        sys.exit("환경변수 GEMINI_API_KEY 와 DATA_GO_KR_KEY 가 필요합니다.")

    try:
        with open(OUT, encoding="utf-8") as f:
            items = json.load(f).get("items", {})
    except (OSError, ValueError):
        items = {}
    services = sorted(_welfare._fetch_all("serviceList"), key=lambda s: -(_welfare._num(s.get("조회수")) or 0))
    live = {s["서비스ID"] for s in services}
    for gone in set(items) - live:  # 사라진 서비스 정리
        del items[gone]
    todo = []
    for s in services:
        src = source_text(s)
        if items.get(src["id"], {}).get("h") != fingerprint(src):
            todo.append((src, fingerprint(src)))
    if a.limit:
        todo = todo[:a.limit]
    print(f"전체 {len(services)}건, 이번 처리 {len(todo)}건, 모델 {MODEL}", flush=True)

    lock = threading.Lock()
    deadline = time.time() + a.max_minutes * 60 if a.max_minutes else None
    stats = {"done": 0, "fail": 0}

    def save():
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "model": MODEL, "items": items}, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, OUT)

    def work(chunk):
        if deadline and time.time() > deadline:
            return
        batch = [c[0] for c in chunk]
        try:
            res = call_gemini(batch, key)
        except Exception as e:
            with lock:
                stats["fail"] += len(chunk)
                print("실패:", type(e).__name__, str(e)[:160], flush=True)
            return
        by_id = {r.get("id"): r for r in res if isinstance(r, dict)}
        with lock:
            for src, h in chunk:
                r = by_id.get(src["id"])
                if r is None:
                    stats["fail"] += 1
                    continue
                items[src["id"]] = {"h": h, **clean(r)}
                stats["done"] += 1
            if stats["done"] % 60 < len(chunk):
                save()
                print(f"진행 {stats['done']}/{len(todo)} (실패 {stats['fail']})", flush=True)

    chunks = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, chunks))
    save()
    print(f"완료: 성공 {stats['done']}, 실패 {stats['fail']}, 저장 {len(items)}건 -> {OUT}")


if __name__ == "__main__":
    main()
