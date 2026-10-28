from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs
import concurrent.futures

def fetch_symbol_data(sym, interval):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    try:
        # 🚨 修正核心：當前端沒有指定 interval (純粹抓最新現價) 時，
        # 強制使用 1m (1分鐘線)，逼迫 Yahoo 吐出包含盤前盤後的真實 tick 陣列！
        is_live_poll = (not interval)
        if is_live_poll:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1m&includePrePost=true"
        else:
            yf_interval, yf_range = "1d", "6mo"
            if interval == '15m': yf_interval, yf_range = "15m", "20d"  
            elif interval == '4h': yf_interval, yf_range = "60m", "3mo"
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range={yf_range}&interval={yf_interval}&includePrePost=true"
            
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            
        chart_result = data['chart']['result'][0]
        meta = chart_result['meta']
        
        def safe_get(d, k, default=0):
            v = d.get(k)
            return v if v is not None else default

        prev_close = safe_get(meta, 'chartPreviousClose', safe_get(meta, 'previousClose'))
        reg_price = safe_get(meta, 'regularMarketPrice', 0)
        
        final_price = 0
        kline_data = []
        
        if 'timestamp' in chart_result and 'indicators' in chart_result and 'quote' in chart_result['indicators']:
            timestamps = chart_result['timestamp']
            quotes = chart_result['indicators']['quote'][0]
            
            closes = quotes.get('close', [])
            valid_closes = [c for c in closes if c is not None]
            
            # 🚨 終極解法：直接從 1 分鐘線陣列抓取最後一筆有效報價
            if is_live_poll and valid_closes:
                final_price = valid_closes[-1]
                
            # 若為前端請求畫圖 (有指定 interval)
            if not is_live_poll:
                for i in range(len(timestamps)):
                    if quotes.get('open') and quotes.get('close') and quotes['open'][i] is not None and quotes['close'][i] is not None:
                        kline_data.append({
                            "time": timestamps[i], 
                            "open": round(quotes['open'][i], 2),
                            "high": round(quotes['high'][i], 2),
                            "low": round(quotes['low'][i], 2),
                            "close": round(quotes['close'][i], 2),
                        })

        # 雙重保險：如果陣列讀取失敗，退回使用 metadata 時間戳比較法
        if not final_price:
            prices = [
                (safe_get(meta, 'regularMarketTime'), safe_get(meta, 'regularMarketPrice')),
                (safe_get(meta, 'preMarketTime'), safe_get(meta, 'preMarketPrice')),
                (safe_get(meta, 'postMarketTime'), safe_get(meta, 'postMarketPrice'))
            ]
            prices = [p for p in prices if p[0] > 0 and p[1] > 0]
            prices.sort(key=lambda x: x[0], reverse=True)
            final_price = prices[0][1] if prices else reg_price

        change = final_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
        
        res = {
            'price': round(final_price, 2),
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
            'volume': safe_get(meta, 'regularMarketVolume', 0)
        }
        if not is_live_poll: res['chart'] = kline_data
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
