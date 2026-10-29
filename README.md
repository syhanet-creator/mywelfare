# mywelfare — 나의 복지 찾기

나이·성별·소득·상황을 넣으면 받을 수 있는 정부·지자체 공공서비스(혜택)를 찾아 주는 검색 엔진.
자료: 행정안전부 「대한민국 공공서비스(혜택) 정보」 오픈API (공공데이터포털 15113968, gov24 v3 `serviceList`·`supportConditions`).

## 실행
```
DATA_GO_KR_KEY=<공공데이터포털 인증키> python server.py
```
Vercel 배포 시에는 환경변수 `DATA_GO_KR_KEY` 를 등록합니다. Python 표준 라이브러리만 사용합니다.

- 서비스 목록을 한 번 내려받아 서버 메모리에 6시간 보관하고, 검색·거름은 서버에서 합니다.
- 나이·성별·소득은 `supportConditions` 로 맞지 않는 것만 제외하고, 조건 정보가 비어 있으면 통과시킵니다(참고용).

## 자격조건 AI 구조화 (Gemini)
서비스 본문의 지원대상·선정기준을 Gemini 로 읽어 나이·성별·소득 상한·지역·대상 집단·요건을 JSON(`data/eligibility.json`)으로 저장하고,
검색할 때 이 값으로 내 정보와 정확히 비교합니다. 결과는 **해당 / 확인 필요 / 비해당**으로 나뉘고(비해당은 숨김), 카드에 판정 이유가 표시됩니다.
분석되지 않은 서비스는 키워드 기준으로 보여 주며 "조건 자동 분석 전"이라고 표시합니다.

### 실행 (GitHub Actions, 권장)
1. 저장소 Settings → Secrets and variables → Actions → **Secrets** 에 `DATA_GO_KR_KEY`, `GEMINI_API_KEY` 등록
2. (선택) Variables 에 `GEMINI_MODEL` (기본 `gemini-2.5-flash`)
3. Actions 탭 → "자격조건 갱신 (Gemini)" → Run workflow. 처음에는 `limit=60` 으로 시험한 뒤 `0`(전부)으로 실행하세요.
   - 실행이 끝나면 `data/eligibility.json` 이 커밋되고 Vercel 이 자동 배포합니다. 매주 월요일에도 자동으로 바뀐 서비스만 갱신합니다.

### 로컬 실행
```
DATA_GO_KR_KEY=... GEMINI_API_KEY=... python tools/extract_eligibility.py --limit 60
```
