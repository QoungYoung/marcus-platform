"""东财 API 代理服务 — 监听本地端口，转发到 push2.eastmoney.com"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import json
import requests as req

COOKIE = "qgqp_b_id=1cc3c89ff09003f14504d6ce2704f978; st_nvi=W6lpD9Ad7PhFwtvK87DTf930b; nid18=0669c78d6e75a0345b1571c451cbd4b4; nid18_create_time=1777289270410; gviem=K3qwW0bI41sVLDrtqtPBQ2d3c; gviem_create_time=1777289270410"

PORT = 8199

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        backend_url = f"https://push2.eastmoney.com{parsed.path}"
        if parsed.query:
            backend_url += f"?{parsed.query}"

        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        try:
            resp = req.get(backend_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
                "Accept": "*/*",
                "Cookie": COOKIE,
                "Referer": "https://data.eastmoney.com/zjlx/detail.html",
            }, timeout=15)
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp.content)
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        print(f"[em_proxy] {self.client_address[0]} - {args[0]}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"[em_proxy] 东财代理服务已启动: http://localhost:{PORT}")
    print(f"[em_proxy] 转发目标: push2.eastmoney.com")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[em_proxy] 已停止")
