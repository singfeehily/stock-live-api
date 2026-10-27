from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from urllib.parse import urlparse, parse_qs

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
        # 過濾空白並轉大寫
        symbols = [s.strip().upper() for s in symbols_str.split(',') if s.strip()]
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        
        try:
            # ⚡ 模式 1：純粹抓取即時報價 (給 10 秒自動更新用的，速度極快，支援多檔查詢)
            if not interval:
                symbols_query = ",".join(symbols)
                url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_query}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    
                for item in data.get('quoteResponse', {}).get('result', []):
                    sym = item.get('symbol')
                    reg_price = item.get('regularMarketPrice', 0)
                    post_price = item.get('postMarketPrice')
                    pre_price = item.get('preMarketPrice')
                    market_state = item.get('marketState', 'REGULAR')
                    
                    # 智能盤前/盤後價判定
                    if market_state == 'PRE' and pre_price:
                        price = pre_price
                    elif market_state in ['POST', 'POSTPOST', 'CLOSED'] and post_price:
                        price = post_price
                    else:
                        price = reg_price
                        
                    # 防呆：如果 API 沒回傳 state 但有盤後價
                    if price == 0 and reg_price > 0:
                        price = post_price if post_price else reg_price
                        
                    prev_close = item.get('regularMarketPreviousClose', reg_price)
                    change = price - prev_close
                    change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                    
                    results[sym] = {
                        'price': round(price, 2),
                        'change': round(change, 2),
                        'change_pct': round(change_pct, 2),
                        'volume': item.get('regularMarketVolume', 0)
                    }
                    
            # ⚡ 模式 2：抓取 K 線歷史數據 (給使用者點擊股票換圖表用的)
            else:
                yf_interval, yf_range = "1d", "6mo"
                if interval == '15m': yf_interval, yf_range = "15m", "20d"  
                elif interval == '4h': yf_interval, yf_range = "60m", "3mo"
                    
                for sym in symbols:
                    # 1. 先快速抓取精準現價 (確保圖表上的 Live Price 標籤也是盤外價)
                    quote_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym}"
                    req_q = urllib.request.Request(quote_url, headers=headers)
                    price = 0
                    prev_close = 0
                    try:
                        with urllib.request.urlopen(req_q) as response_q:
                            q_data = json.loads(response_q.read().decode())
                            if q_data.get('quoteResponse', {}).get('result'):
                                q_item = q_data['quoteResponse']['result'][0]
                                reg_price = q_item.get('regularMarketPrice', 0)
                                post_price = q_item.get('postMarketPrice')
                                pre_price = q_item.get('preMarketPrice')
                                state = q_item.get('marketState', 'REGULAR')
                                
                                if state == 'PRE' and pre_price: price = pre_price
                                elif state in ['POST', 'POSTPOST', 'CLOSED'] and post_price: price = post_price
                                else: price = reg_price
                                
                                prev_close = q_item.get('regularMarketPreviousClose', reg_price)
                    except: pass

                    # 2. 抓取 K 線圖表陣列
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={yf_range}&interval={yf_interval}"
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req) as response:
                        data = json.loads(response.read().decode())
                        
                    chart_result = data['chart']['result'][0]
                    meta = chart_result['meta']
                    
                    if price == 0: price = meta.get('regularMarketPrice', 0)
                    if prev_close == 0: prev_close = meta.get('chartPreviousClose', price)
                    
                    change = price - prev_close
                    change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                    
                    result_item = {
                        'price': round(price, 2),
                        'change': round(change, 2),
                        'change_pct': round(change_pct, 2),
                        'volume': meta.get('regularMarketVolume', 0)
                    }
                    
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
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(f"Error fetching data: {str(e)}".encode('utf-8'))
