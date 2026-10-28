from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs
import concurrent.futures

def fetch_symbol_data(sym, interval):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    try:
        if not interval:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d&includePrePost=true"
        else:
            yf_interval, yf_range = "1d", "6mo"
            if interval == '15m': yf_interval, yf_range = "15m", "20d"  
            elif interval == '4h': yf_interval, yf_range = "60m", "3mo"
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range={yf_range}&interval={yf_interval}&includePrePost=true"
            
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
            
        chart_result = data['chart']['result'][0]
        meta = chart_result['meta']
        
        # 多重備援提取最新價格與昨收價，確保 % 漲跌幅 100% 正確
        reg_price = meta.get('regularMarketPrice', 0)
        prev_close = meta.get('chartPreviousClose', meta.get('previousClose', reg_price))
        
        last_close = reg_price
        kline_data = []
        
        if 'timestamp' in chart_result and 'indicators' in chart_result and 'quote' in chart_result['indicators']:
            timestamps = chart_result['timestamp']
            quotes = chart_result['indicators']['quote'][0]
            
            # 針對 5d 查詢，強制提取倒數第二根作為絕對昨收價 (避免 API 回傳缺失)
            if not interval and len(timestamps) > 1:
                prev_idx = -2 if quotes['close'][-1] is not None else -3
                if abs(prev_idx) <= len(quotes['close']):
                    fallback_prev = quotes['close'][prev_idx]
                    if fallback_prev is not None and prev_close == reg_price:
                        prev_close = fallback_prev

            for i in range(len(timestamps)):
                if quotes['open'][i] is not None and quotes['close'][i] is not None:
                    last_close = quotes['close'][i]
                    if interval:
                        kline_data.append({
                            "time": timestamps[i], 
                            "open": round(quotes['open'][i], 2),
                            "high": round(quotes['high'][i], 2),
                            "low": round(quotes['low'][i], 2),
                            "close": round(quotes['close'][i], 2),
                        })
                        
        final_price = last_close
        change = final_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
        
        res = {
            'price': round(final_price, 2),
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
            'volume': meta.get('regularMarketVolume', 0)
        }
        if interval: res['chart'] = kline_data
        return sym, res
    except Exception as e:
        return sym, None

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        symbols_str = query_components.get("symbols", [""])[0]
        interval = query_components.get("interval", [""])[0].lower()
        
        if not symbols_str:
            self.send_response(400)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write("請提供股票代號".encode('utf-8'))
            return

        results = {}
        symbols = [s.strip().upper() for s in symbols_str.split(',') if s.strip()]
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(fetch_symbol_data, sym, interval) for sym in symbols]
                for future in concurrent.futures.as_completed(futures):
                    sym, res = future.result()
                    if res: results[sym] = res
                        
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
            self.end_headers()
            self.wfile.write(f"Server Error: {str(e)}".encode('utf-8'))
