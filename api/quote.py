from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs
from datetime import datetime

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        symbols_str = query_components.get("symbols", [""])[0]
        
        # 接收前端傳來的新參數：interval
        interval = query_components.get("interval", [""])[0].lower()
        
        if not symbols_str:
            self.send_response(400)
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.end_headers()
            self.wfile.write("請提供股票代號, 例如: ?symbols=AAPL".encode('utf-8'))
            return

        results = {}
        symbols = symbols_str.split(',')
        
        # 動態決定 Yahoo API 參數
        yf_interval = "1d"
        yf_range = "1d"
        
        if interval == '15m':
            yf_interval = "15m"
            yf_range = "20d"  
        elif interval == '4h':
            yf_interval = "60m"
            yf_range = "3mo"
        elif interval == '1d':
            yf_interval = "1d"
            yf_range = "6mo"
            
        try:
            for sym in symbols:
                sym = sym.strip().upper()
                if not sym: continue
                
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={yf_range}&interval={yf_interval}"
                
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                req = urllib.request.Request(url, headers=headers)
                
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    
                chart_result = data['chart']['result'][0]
                meta = chart_result['meta']
                
                # 1. 提取即時報價
                price = meta.get('regularMarketPrice', 0)
                prev_close = meta.get('previousClose', price)
                change = price - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                volume = meta.get('regularMarketVolume', 0)
                
                result_item = {
                    'price': round(price, 2),
                    'change': round(change, 2),
                    'change_pct': round(change_pct, 2),
                    'volume': volume
                }
                
                # 2. 如果前端有要求 interval，打包 K 線數據
                if interval in ['15m', '4h', '1d']:
                    kline_data = []
                    if 'timestamp' in chart_result and 'indicators' in chart_result and 'quote' in chart_result['indicators']:
                        timestamps = chart_result['timestamp']
                        quotes = chart_result['indicators']['quote'][0]
                        
                        for i in range(len(timestamps)):
                            if quotes['open'][i] is not None and quotes['close'][i] is not None:
                                kline_data.append({
                                    "time": timestamps[i], 
                                    "open": round(quotes['open'][i], 2),
                                    "high": round(quotes['high'][i], 2),
                                    "low": round(quotes['low'][i], 2),
                                    "close": round(quotes['close'][i], 2)
                                })
                    
                    result_item['chart'] = kline_data
                
                results[sym] = result_item
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            # ⚡ 破壞緩存：強制瀏覽器與邊緣節點不可暫存此 API
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.end_headers()
            self.wfile.write(json.dumps(results).encode('utf-8'))
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.end_headers()
            self.wfile.write(f"Error fetching data: {str(e)}".encode('utf-8'))
