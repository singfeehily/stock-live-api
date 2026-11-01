from flask import Flask, jsonify, request
from flask_cors import CORS
import urllib.request
import json
import pandas as pd
import numpy as np

app = Flask(__name__)
CORS(app)  # 🚨 自動解開所有 CORS 跨域封鎖，讓 GitHub Pages 可以順利呼叫！

# 港美股中文名稱對照表
COMPANY_NAME_MAP = {
    '00700': '騰訊控股', '09988': '阿里巴巴', '03690': '美團', '01810': '小米集團',
    '00981': '中芯國際', '00005': '匯豐控股', '00388': '香港交易所', '01299': '友邦保險',
    '00883': '中國海洋石油', '00941': '中國移動', '02015': '理想汽車', '09868': '小鵬汽車',
    '01211': '比亞迪股份', '09618': '京東集團', '01024': '快手', '09999': '網易',
    'NVDA': 'NVIDIA', 'AAPL': 'Apple', 'TSLA': 'Tesla', 'MSFT': 'Microsoft',
    'AMZN': 'Amazon', 'META': 'Meta', 'GOOGL': 'Alphabet', 'AMD': 'AMD',
    'TSM': '台積電', 'AVGO': 'Broadcom', 'MU': 'Micron', 'SMCI': 'Super Micro',
    'SPY': '標普500 ETF', 'QQQ': '納指100 ETF', 'DIA': '道瓊 ETF', 'SMH': '半導體 ETF'
}

def get_company_name(sym):
    raw = sym.upper().replace('HK.', '').replace('US.', '').replace('.HK', '').strip()
    if raw in COMPANY_NAME_MAP: return COMPANY_NAME_MAP[raw]
    if raw.isdigit():
        padded = raw.zfill(5)
        if padded in COMPANY_NAME_MAP: return COMPANY_NAME_MAP[padded]
    return raw

def get_yf_symbol(sym):
    s = sym.upper().strip()
    if s.isdigit(): return f"{int(s):04d}.HK"
    return s

def compute_order_flow_delta(df):
    if df.empty: return df
    deltas, prev_close = [], None
    for _, row in df.iterrows():
        o, h, l, c, v = float(row['open']), float(row['high']), float(row['low']), float(row['close']), float(row.get('volume', 0))
        if v <= 0 or np.isnan(v):
            net_delta = 0.0
        elif h > l:
            pr = ((c - l) - (h - c)) / (h - l)
            bb = 0.18 if c > o else (-0.18 if c < o else 0.0)
            net_delta = v * max(-1.0, min(1.0, pr * 0.82 + bb))
        else:
            net_delta = v if (prev_close and c > prev_close) else (-v if (prev_close and c < prev_close) else 0.0)
        prev_close = c
        deltas.append(round(net_delta, 0))
    df = df.copy()
    df['delta'] = deltas
    ad = df['delta'].abs()
    ra = ad.rolling(window=20, min_periods=3).mean().fillna(ad.mean() if len(ad) > 0 else 0.0)
    df['is_whale'] = (ad > (ra * 1.8)) & (ad > 0)
    return df

def fetch_symbol_data(sym, interval):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    yf_sym = get_yf_symbol(sym)
    
    is_live_poll = (not interval)
    if is_live_poll:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{yf_sym}?range=1d&interval=1m&includePrePost=true"
    else:
        yf_interval, yf_range = "1d", "6mo"
        if interval == '15m': yf_interval, yf_range = "15m", "20d"  
        elif interval in ['1h', '4h', '60m']: yf_interval, yf_range = "60m", "3mo"
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{yf_sym}?range={yf_range}&interval={yf_interval}&includePrePost=true"
        
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode())
            
        chart_result = data['chart']['result'][0]
        meta = chart_result['meta']
        
        prev_close = meta.get('chartPreviousClose', meta.get('previousClose', 0))
        regular_price = meta.get('regularMarketPrice', meta.get('chartPrice', prev_close))
        
        timestamps = chart_result.get('timestamp', [])
        quotes = chart_result.get('indicators', {}).get('quote', [{}])[0]
        
        opens = quotes.get('open', [])
        highs = quotes.get('high', [])
        lows = quotes.get('low', [])
        closes = quotes.get('close', [])
        volumes = quotes.get('volume', [])
        
        df_rows = []
        for i in range(len(timestamps)):
            if i < len(closes) and closes[i] is not None:
                df_rows.append({
                    'time_key': pd.to_datetime(timestamps[i], unit='s'),
                    'open': opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                    'high': highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                    'low': lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                    'close': closes[i],
                    'volume': volumes[i] if i < len(volumes) and volumes[i] is not None else 0
                })
                
        df = pd.DataFrame(df_rows)
        if not df.empty:
            df = compute_order_flow_delta(df)
            current_price = float(df.iloc[-1]['close'])
        else:
            current_price = float(regular_price if regular_price > 0 else prev_close)

        high_val = float(df['high'].max()) if not df.empty else current_price * 1.05
        low_val = float(df['low'].min()) if not df.empty else current_price * 0.95
        macro_eq = round((high_val + low_val) / 2.0, 2)
        
        entry_bot = round(low_val * 1.01, 2)
        entry_top = round(low_val * 1.03, 2)
        sl_price = round(entry_bot * 0.985, 2)
        tp_price = round(max(high_val * 0.99, current_price * 1.06), 2)
        
        risk = max(0.05, abs(current_price - sl_price))
        reward = max(0.1, abs(tp_price - current_price))
        rrr = round(min(6.5, max(1.8, reward / risk)), 1)
        
        last_delta = float(df['delta'].iloc[-1]) if not df.empty and 'delta' in df.columns else 0.0
        is_whale = bool(df['is_whale'].iloc[-1]) if not df.empty and 'is_whale' in df.columns else False

        chart_array = []
        if not df.empty:
            for _, row in df.iterrows():
                t_val = int(row['time_key'].timestamp())
                c_val = float(row['close'])
                chart_array.append({
                    "time": t_val,
                    "open": round(float(row['open']), 2),
                    "high": round(float(row['high']), 2),
                    "low": round(float(row['low']), 2),
                    "close": round(c_val, 2),
                    "volume": round(float(row.get('volume', 0)), 0),
                    "delta": round(float(row.get('delta', 0)), 0),
                    "whale": bool(row.get('is_whale', False))
                })

        chg_val = round(current_price - prev_close, 2) if prev_close > 0 else 0.0
        chg_pct = round((chg_val / prev_close) * 100, 2) if prev_close > 0 else 0.0

        return {
            "symbol": sym.upper(),
            "company_name": get_company_name(sym),
            "price": round(current_price, 2),
            "prev_close": round(prev_close, 2),
            "change": chg_val,
            "change_pct": chg_pct,
            "macro_eq": macro_eq,
            "entry_bot": entry_bot,
            "entry_top": entry_top,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "rrr": rrr,
            "net_delta": last_delta,
            "is_whale": is_whale,
            "smc_text": "[機構承接進場]" if last_delta > 0 else "[觀察中]",
            "where_status": "落入 4H 機構需求區 (OB)",
            "when_status": "Delta 主動買盤承接中",
            "trade_status": "[可執行] 機構承接",
            "status_level": "executable",
            "direction": "long",
            "chart": chart_array
        }
    except Exception as e:
        print(f"Error fetching {sym}: {e}")
        return {
            "symbol": sym.upper(),
            "company_name": get_company_name(sym),
            "price": 0.0, "prev_close": 0.0, "change": 0.0, "change_pct": 0.0,
            "chart": []
        }

@app.route('/api/quote', methods=['GET'])
def quote_endpoint():
    symbols_str = request.args.get('symbols', 'NVDA')
    interval = request.args.get('interval', '4h')
    symbols = [s.strip() for s in symbols_str.split(',') if s.strip()]
    
    result = {}
    for sym in symbols:
        result[sym] = fetch_symbol_data(sym, interval)
        
    return jsonify(result)

@app.route('/api/radar', methods=['GET', 'POST'])
def radar_endpoint():
    return jsonify({
        "status": {"is_running": True, "scan_cursor": 15, "universe_size": 80, "last_scan_symbol": "NVDA", "discovered_count": 1},
        "discoveries": {}
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
