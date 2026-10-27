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
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write("請提供股票代號".encode('utf-8'))
            return

        results = {}
        symbols = [s.strip().upper() for s in symbols_str.split(',') if s.strip()]
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        try:
            for sym in symbols:
                try:
                    if not interval:
                        # ⚡ 模式 1：穩定抓取即時報價 (給 10 秒自動更新用)
                        # 使用 1d 範圍, 1m K線, 並強制包含盤前/盤後數據 (避開 v7 驗證)
                        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1m&includePrePost=true"
                        req = urllib.request.Request(url, headers=headers)
                        with urllib.request.urlopen(req) as response:
                            data = json.loads(response.read().decode())
                            
                        meta = data['chart']['result'][0]['meta']
                        
                        # 取得常規、盤前、盤後價格
                        reg_price = meta.get('regularMarketPrice', 0)
                        post_price = meta.get('postMarketPrice')
                        pre_price = meta.get('preMarketPrice')
                        
                        # 從 1 分鐘 K 線陣列抓取絕對最新的一筆價格 (雙重保險)
                        latest_k_price = reg_price
                        try:
                            quotes = data['chart']['result'][0]['indicators']['quote'][0]
                            valid_closes = [c for c in quotes.get('close', []) if c is not None]
                            if valid_closes:
                                latest_k_price = valid_closes[-1]
                        except: pass

                        # 判斷最終價格 (盤前 > 盤後 > 最新K線 > 常規收盤)
                        if pre_price:
                            final_price = pre_price
                        elif post_price:
                            final_price = post_price
                        else:
                            final_price = latest_k_price if latest_k_price > 0 else reg_price
                            
                        prev_close = meta.get('chartPreviousClose', meta.get('previousClose', reg_price))
                        change = final_price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                        
                        results[sym] = {
                            'price': round(final_price, 2),
                            'change': round(change, 2),
                            'change_pct': round(change_pct, 2),
                            'volume': meta.get('regularMarketVolume', 0)
                        }
                        
                    else:
                        # ⚡ 模式 2：抓取 K 線歷史圖表 (給使用者切換週期用)
                        yf_interval, yf_range = "1d", "6mo"
                        if interval == '15m': yf_interval, yf_range = "15m", "20d"  
                        elif interval == '4h': yf_interval, yf_range = "60m", "3mo"
                            
                        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={yf_range}&interval={yf_interval}&includePrePost=true"
                        req = urllib.request.Request(url, headers=headers)
                        with urllib.request.urlopen(req) as response:
                            data = json.loads(response.read().decode())
                            
                        chart_result = data['chart']['result'][0]
                        meta = chart_result['meta']
                        
                        reg_price = meta.get('regularMarketPrice', 0)
                        post_price = meta.get('postMarketPrice')
                        pre_price = meta.get('preMarketPrice')
                        
                        final_price = pre_price if pre_price else (post_price if post_price else reg_price)
                        prev_close = meta.get('chartPreviousClose', meta.get('previousClose', reg_price))
                        
                        change = final_price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                        
                        result_item = {
                            'price': round(final_price, 2),
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
                
                except Exception as inner_e:
                    # 💡 獨立保護罩：某檔股票抓取失敗，只會印出錯誤，不會中斷整個迴圈
                    print(f"Error fetching {sym}: {inner_e}")
                    continue
                    
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
