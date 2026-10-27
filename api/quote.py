from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs
from datetime import datetime

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        symbols_str = query_components.get("symbols", [""])[0]
        interval = query_components.get("interval", [""])[0].lower()
        
        if not symbols_str:
            self.send_response(400)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write("請提供股票代號, 例如: ?symbols=AAPL".encode('utf-8'))
            return

        results = {}
        symbols = symbols_str.split(',')
        
        yf_interval = "1d"
        yf_range = "1d"
        
        if interval == '15m': 
            yf_interval, yf_range = "15m", "20d"  
        elif interval == '4h': 
            yf_interval, yf_range = "60m", "3mo"
        elif interval == '1d': 
            yf_interval, yf_range = "1d", "6mo"
            
        try:
            for sym in symbols:
                sym = sym.strip().upper()
                if not sym: continue
                
                # ⚡ 核心修正 1：加上 includePrePost=true 參數，強迫 API 吐出盤外數據
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={yf_range}&interval={yf_interval}&includePrePost=true"
                
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                req = urllib.request.Request(url, headers=headers)
                
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    
                chart_result = data['chart']['result'][0]
                meta = chart_result['meta']
                
                # ⚡ 核心修正 2：智能盤前/盤後價格覆蓋邏輯
                regular_price = meta.get('regularMarketPrice', 0)
                post_price = meta.get('postMarketPrice')
                pre_price = meta.get('preMarketPrice')
                
                # 優先級：盤前 > 盤後 > 常規時段
                price = pre_price if pre_price else (post_price if post_price else regular_price)
                
                # 昨收價計算 (確保漲跌幅準確)
                prev_close = meta.get('chartPreviousClose', meta.get('previousClose', regular_price))
                
                change = price - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                volume = meta.get('regularMarketVolume', 0)
                
                result_item = {
                    'price': round(price, 2),
                    'change': round(change, 2),
                    'change_pct': round(change_pct, 2),
                    'volume': volume
                }
                
                # K線數據處理
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
