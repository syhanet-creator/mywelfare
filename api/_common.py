import json


def send_json(h, obj, cache_control="no-store", status=200):
    body = json.dumps(obj, ensure_ascii=False).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Cache-Control", cache_control)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)
