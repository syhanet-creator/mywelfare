"""로컬 실행: DATA_GO_KR_KEY=인증키 python server.py  ->  http://127.0.0.1:8766/"""
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from api import _welfare

PUBLIC = Path(__file__).parent / "public"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/welfare":
            body, ctype = json.dumps(_welfare.from_query(self.path), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        else:
            f = (PUBLIC / ("index.html" if u.path == "/" else u.path.lstrip("/"))).resolve()
            if PUBLIC.resolve() not in f.parents or not f.is_file():
                return self.send_error(404)
            body = f.read_bytes()
            ctype = {".html": "text/html; charset=utf-8", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("http://127.0.0.1:8766/")
    ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
