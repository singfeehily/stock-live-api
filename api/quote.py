from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 1. 解析網址後面的股票代號參數 (例如: ?symbols=AAPL,NVDA)
        query_components = parse_qs(urlparse(self.path).query)
        symbols = query_components.get("symbols", [""])[0]
        
        if not symbols:
            self.send_response(400)
            self.end_headers()
            self.wfile.write("請提供股票代號, 例如: ?symbols=AAPL".encode('utf-8'))
            return

        try:
            # 2. 直接呼叫 Yahoo Finance 最底層的隱藏即時 API
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            req = urllib.request.Request(url, headers=headers)
            
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
            
            # 3. 整理出我們需要的：現價、漲跌幅、成交量
            results = {}
            for item in data['quoteResponse']['result']:
                results[item['symbol']] = {
                    'price': round(item.get('regularMarketPrice', 0), 2),
                    'change': round(item.get('regularMarketChange', 0), 2),
                    'change_pct': round(item.get('regularMarketChangePercent', 0), 2),
                    'volume': item.get('regularMarketVolume', 0)
                }
            
            # 4. 回傳給前端 (加入 CORS 允許你的 GitHub Pages 讀取)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*') # 允許跨網域
            self.end_headers()
            self.wfile.write(json.dumps(results).encode('utf-8'))
            
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))
