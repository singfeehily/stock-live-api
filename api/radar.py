from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        response_data = {
            "status": {"is_running": True, "scan_cursor": 15, "universe_size": 80, "last_scan_symbol": "NVDA", "discovered_count": 1},
            "discoveries": {}
        }
        self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))

    def do_POST(self):
        self.do_GET()
