import os
import sys
from http.server import BaseHTTPRequestHandler

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.dirname(_here)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _common import send_json  # noqa: E402
import _welfare  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        res = _welfare.from_query(self.path)
        # 같은 조건의 검색은 1시간 공유(공공데이터포털 호출 횟수·응답 시간 절약). 오류는 캐시하지 않는다.
        ok = not res.get("error")
        send_json(self, res, "public, s-maxage=3600, stale-while-revalidate=21600" if ok else "no-store")
