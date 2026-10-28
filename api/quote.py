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
        
        # 安全獲取字典數據的函數防呆
        def safe_get(d, k, default=0):
            v = d.get(k)
            return v if v is not None else default

        reg_price = safe_get(meta, 'regularMarketPrice', 0)
        prev_close = safe_get(meta, 'chartPreviousClose', safe_get(meta, 'previousClose', reg_price))
        
        # 🚨 終極延伸時段 (Pre/Post Market) 追蹤引擎：比對時間戳，永遠只抓最新一筆報價
        prices = [
            (safe_get(meta, 'regularMarketTime'), safe_get(meta, 'regularMarketPrice')),
            (safe_get(meta, 'preMarketTime'), safe_get(meta, 'preMarketPrice')),
            (safe_get(meta, 'postMarketTime'), safe_get(meta, 'postMarketPrice'))
        ]
        # 過濾掉無效資料並依時間戳 (最新到最舊) 排序
        prices = [p for p in prices if p[0] > 0 and p[1] > 0]
        prices.sort(key=lambda x: x[0], reverse=True)
        
        # 取得絕對最新價格 (盤前、盤中、盤後無縫切換)
        final_price = prices[0][1] if prices else reg_price
        
        kline_data = []
        if 'timestamp' in chart_result and 'indicators' in chart_result and 'quote' in chart_result['indicators']:
            timestamps = chart_result['timestamp']
            quotes = chart_result['indicators']['quote'][0]
            
            # 若為請求日線，強制追溯昨收價，防止漲跌幅 0% 錯誤
            if not interval and len(timestamps) > 1:
                prev_idx = -2 if quotes['close'][-1] is not None else -3
                if abs(prev_idx) <= len(quotes['close']):
                    fallback_prev = quotes['close'][prev_idx]
                    if fallback_prev is not None and prev_close == reg_price:
                        prev_close = fallback_prev

            # 圖表 K 線數據
            if interval:
                for i in range(len(timestamps)):
                    if quotes['open'][i] is not None and quotes['close'][i] is not None:
                        kline_data.append({
                            "time": timestamps[i], 
                            "open": round(quotes['open'][i], 2),
                            "high": round(quotes['high'][i], 2),
                            "low": round(quotes['low'][i], 2),
                            "close": round(quotes['close'][i], 2),
                        })
                        
        change = final_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
        
        res = {
            'price': round(final_price, 2),
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
            'volume': safe_get(meta, 'regularMarketVolume', 0)
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
