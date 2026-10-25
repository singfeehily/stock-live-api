from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        symbols_str = query_components.get("symbols", [""])[0]
        
        if not symbols_str:
            self.send_response(400)
            self.end_headers()
            self.wfile.write("請提供股票代號, 例如: ?symbols=AAPL".encode('utf-8'))
            return

        results = {}
        symbols = symbols_str.split(',')
        
        try:
            for sym in symbols:
                sym = sym.strip().upper()
                if not sym: continue
                
                # 秘密武器：改用 v8 chart API，完美繞過 Yahoo 的 401 憑證阻擋
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"
                
                # 偽裝成真實瀏覽器
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                req = urllib.request.Request(url, headers=headers)
                
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    
                # 從圖表元數據 (meta) 中精準提取現價與昨收
                meta = data['chart']['result'][0]['meta']
                price = meta.get('regularMarketPrice', 0)
                prev_close = meta.get('previousClose', price)
                
                change = price - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                volume = meta.get('regularMarketVolume', 0)
                
                results[sym] = {
                    'price': round(price, 2),
                    'change': round(change, 2),
                    'change_pct': round(change_pct, 2),
                    'volume': volume
                }
            
            # 回傳給前端，並允許 Dashboard 跨網域讀取
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(results).encode('utf-8'))
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(f"Error fetching data: {str(e)}".encode('utf-8'))
