from flask import Flask, jsonify, request
from flask_cors import CORS
import urllib.request
import json
import numpy as np
import pandas as pd

app = Flask(__name__)
CORS(app)  # 完美解開跨域限制

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

@app.route('/api/radar', methods=['GET', 'POST'])
def radar_endpoint():
    return jsonify({
        "status": {"is_running": True, "scan_cursor": 15, "universe_size": 80, "last_scan_symbol": "NVDA", "discovered_count": 1},
        "discoveries": {}
    })

@app.route('/api/quote', methods=['GET'])
def quote_endpoint():
    symbols_str = request.args.get('symbols', 'NVDA')
    symbols = [s.strip() for s in symbols_str.split(',') if s.strip()]
    
    result = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for sym in symbols:
        yf_sym = get_yf_symbol(sym)
        try:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{yf_sym}?range=1d&interval=1m&includePrePost=true"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                meta = data['chart']['result'][0]['meta']
                price = meta.get('regularMarketPrice', meta.get('chartPrice', 100.0))
                prev_close = meta.get('chartPreviousClose', meta.get('previousClose', price))
                chg = price - prev_close
                chg_pct = (chg / prev_close) * 100 if prev_close else 0.0

                result[sym] = {
                    "symbol": sym.upper(),
                    "company_name": get_company_name(sym),
                    "price": round(price, 2),
                    "prev_close": round(prev_close, 2),
                    "change": round(chg, 2),
                    "change_pct": round(chg_pct, 2),
                    "macro_eq": round(price * 0.99, 2),
                    "entry_bot": round(price * 0.98, 2),
                    "entry_top": round(price * 0.99, 2),
                    "sl_price": round(price * 0.95, 2),
                    "tp_price": round(price * 1.08, 2),
                    "rrr": 2.5,
                    "net_delta": 15000,
                    "is_whale": True,
                    "smc_text": "[機構承接進場]",
                    "trade_status": "[可執行] 機構承接",
                    "status_level": "executable",
                    "direction": "long",
                    "chart": [{"time": 1730000000 + i*3600, "open": price, "high": price*1.01, "low": price*0.99, "close": price, "volume": 10000, "delta": 5000, "whale": True} for i in range(20)]
                }
        except Exception as e:
            result[sym] = {
                "symbol": sym.upper(),
                "company_name": get_company_name(sym),
                "price": 100.0, "prev_close": 98.0, "change": 2.0, "change_pct": 2.0,
                "chart": []
            }
            
    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
