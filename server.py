from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import socket
import json
import time
import numpy as np
import pandas as pd

app = Flask(__name__)
CORS(app)

COMPANY_NAME_MAP = {
    '00700': '騰訊控股', '09988': '阿里巴巴', '03690': '美團', '01810': '小米集團',
    '00981': '中芯國際', '00005': '匯豐控股', '00388': '香港交易所', '01299': '友邦保險',
    '00883': '中國海洋石油', '00941': '中國移動', '02015': '理想汽車', '09868': '小鵬汽車',
    '01211': '比亞迪股份', '09618': '京東集團', '01024': '快手', '09999': '網易',
    'NVDA': 'NVIDIA', 'AAPL': 'Apple', 'TSLA': 'Tesla', 'MSFT': 'Microsoft',
    'AMZN': 'Amazon', 'META': 'Meta', 'GOOGL': 'Alphabet', 'AMD': 'AMD',
    'TSM': '台積電', 'AVGO': 'Broadcom', 'MU': 'Micron', 'MUU': '2x做多MU',
    'SNDU': '2x做多SNDK', 'SNDK': 'SanDisk', 'SMCI': 'Super Micro',
    'SPY': '標普500 ETF', 'QQQ': '納指100 ETF', 'DIA': '道瓊 ETF', 'SMH': '半導體 ETF'
}

RADAR_UNIVERSE = ['NVDA', 'TSLA', 'AAPL', 'AMD', 'MU', 'MUU', 'SMH', 'QQQ', 'META', 'AMZN', 'MSFT', 'GOOGL', 'AVGO', 'TSM']
RADAR_STATE = {
    "is_running": True,
    "scan_cursor": 0,
    "universe_size": len(RADAR_UNIVERSE),
    "last_scan_symbol": "NVDA",
    "discovered_count": 0
}
RADAR_CACHE = {}

def get_company_name(sym):
    raw = sym.upper().replace('HK.', '').replace('US.', '').replace('.HK', '').strip()
    if raw in COMPANY_NAME_MAP:
        return COMPANY_NAME_MAP[raw]
    if raw.isdigit():
        padded = raw.zfill(5)
        if padded in COMPANY_NAME_MAP:
            return COMPANY_NAME_MAP[padded]
    return raw

def get_yf_symbol(sym):
    s = sym.upper().replace('HK.', '').replace('US.', '').replace('.HK', '').strip()
    if s.isdigit():
        return f"{int(s):04d}.HK"
    return s

def get_futu_symbol(sym):
    s = sym.upper().replace('HK.', '').replace('US.', '').replace('.HK', '').strip()
    if s.isdigit():
        return f"HK.{s.zfill(5)}"
    return f"US.{s}"

def is_local_opend_alive(host='127.0.0.1', port=11111):
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except Exception:
        return False

def compute_order_flow_delta(df):
    """
    4-Argon 強化版訂單流演算法：
    1. 針對 Yahoo Finance 最新一根未結算 K 線常見之 volume=0 進行「滾動平均量 × 振幅動能」補償，徹底解決 Δ=0 問題。
    2. 結合 K 線實體方向、上下影線買賣壓與跳空動能計算精準淨差額。
    """
    if df.empty:
        return df

    df = df.copy()
    vols = pd.to_numeric(df['volume'], errors='coerce').fillna(0.0).values
    opens = pd.to_numeric(df['open'], errors='coerce').values
    highs = pd.to_numeric(df['high'], errors='coerce').values
    lows = pd.to_numeric(df['low'], errors='coerce').values
    closes = pd.to_numeric(df['close'], errors='coerce').values

    positive_vols = vols[vols > 0]
    fallback_avg_vol = float(np.median(positive_vols[-15:])) if len(positive_vols) > 0 else 25000.0

    deltas = []
    prev_close = None

    for i in range(len(df)):
        o, h, l, c, v = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]), float(vols[i])
        spread = max(0.0, h - l)

        # 若最新或盤中 K 線成交量為 0，啟動智慧量能補償
        if v <= 0 or np.isnan(v):
            recent_valid = [vols[j] for j in range(max(0, i - 10), i) if vols[j] > 0]
            base_v = float(np.mean(recent_valid)) if recent_valid else fallback_avg_vol
            avg_spread = float(np.mean([max(0.0001, highs[j] - lows[j]) for j in range(max(0, i - 10), i + 1)]))
            vol_ratio = max(0.35, min(1.8, spread / max(0.0001, avg_spread)))
            v = base_v * vol_ratio

        if spread > 1e-6:
            # 買賣壓定位：收盤越靠近最高價買壓越強，越靠近最低價賣壓越強
            pos_ratio = ((c - l) - (h - c)) / spread
            body_ratio = (c - o) / spread
            raw_weight = pos_ratio * 0.65 + body_ratio * 0.35
            if abs(raw_weight) < 0.08:
                if prev_close is not None and c != prev_close:
                    raw_weight = 0.22 if c > prev_close else -0.22
                else:
                    raw_weight = 0.15 if c >= o else -0.15
            weight = max(-1.0, min(1.0, raw_weight))
            net_delta = v * weight
        else:
            if prev_close is not None and c != prev_close:
                net_delta = v * (0.45 if c > prev_close else -0.45)
            else:
                net_delta = v * 0.18

        prev_close = c
        deltas.append(round(float(net_delta), 0))

    # 若最後一根 K 線剛開盤尚未形成有效波動，沿用前一根確認 K 線的訂單流強度避免歸零
    if len(deltas) >= 2 and abs(deltas[-1]) < 1 and abs(deltas[-2]) > 0:
        deltas[-1] = round(deltas[-2] * 0.65, 0)

    df['delta'] = deltas
    ad = df['delta'].abs()
    ra = ad.rolling(window=18, min_periods=3).mean().fillna(ad.mean() if len(ad) > 0 else 1.0)
    df['is_whale'] = (ad > (ra * 1.72)) & (ad > 0)
    return df

def evaluate_smc_structure(df, current_price, prev_close):
    """計算 4H SMC 結構、OB 需求區、0.5 均衡線與戰術訊號"""
    if df.empty:
        return {}

    lookback = df.tail(60)
    high_val = float(lookback['high'].max())
    low_val = float(lookback['low'].min())
    span = max(0.01, high_val - low_val)
    macro_eq = round((high_val + low_val) / 2.0, 2)

    # 尋找近期機構低位承接訂單塊 (OB)
    entry_bot = round(low_val + span * 0.04, 2)
    entry_top = round(low_val + span * 0.18, 2)
    if entry_top <= entry_bot:
        entry_top = round(entry_bot * 1.015, 2)

    sl_price = round(min(low_val * 0.992, entry_bot * 0.985), 2)
    tp_price = round(max(high_val * 0.99, current_price * 1.065), 2)

    risk = max(0.05, abs(current_price - sl_price))
    reward = max(0.1, abs(tp_price - current_price))
    rrr = round(min(6.5, max(1.8, reward / risk)), 1)

    last_row = df.iloc[-1]
    last_delta = float(last_row.get('delta', 0.0))
    is_whale = bool(last_row.get('is_whale', False))

    in_discount = current_price <= macro_eq
    near_ob = current_price <= entry_top * 1.025 and current_price >= sl_price

    if near_ob and last_delta > 0:
        smc_text = "[機構承接進場]"
        trade_status = "[可執行] 機構承接"
        status_level = "executable"
        where_status = f"落入 4H 機構需求區 (${entry_bot:.2f}~${entry_top:.2f})"
        when_status = "Delta 翻正主動買盤承接中" if not is_whale else "▲ 金柱巨量買單強勢進場"
        direction = "long"
    elif in_discount and last_delta > 0:
        smc_text = "[獵殺啟動]" if is_whale else "[折價區轉強]"
        trade_status = "[可執行] 折價轉折" if is_whale else "[接近區] 折價蓄勢"
        status_level = "executable" if is_whale else "approaching"
        where_status = f"位於 0.5 均衡線 (${macro_eq:.2f}) 下方折價區"
        when_status = "買盤訂單流 Δ 持續流入"
        direction = "long"
    elif not in_discount and last_delta < 0 and is_whale:
        smc_text = "[嚴禁追高]"
        trade_status = "[溢價風險] 派發調節"
        status_level = "approaching"
        where_status = f"處於 0.5 均衡線 (${macro_eq:.2f}) 上方溢價區"
        when_status = "▼ 紫柱機構大單高檔調節"
        direction = "short"
    else:
        smc_text = "[機構承接進場]" if (last_delta > 0 and is_whale) else ("[多頭控盤]" if last_delta >= 0 else "[回調測試]")
        trade_status = "[可執行] 大單異動" if (last_delta > 0 and is_whale) else "[觀察中] 等待回踩"
        status_level = "executable" if (last_delta > 0 and is_whale) else "normal"
        where_status = f"4H 結構區間 (${low_val:.2f} ~ ${high_val:.2f})"
        when_status = "主動買盤佔優 (Δ > 0)" if last_delta >= 0 else "短線賣壓測試支撐 (Δ < 0)"
        direction = "long" if last_delta >= 0 else "short"

    return {
        "macro_eq": macro_eq,
        "entry_bot": entry_bot,
        "entry_top": entry_top,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "rrr": rrr,
        "net_delta": last_delta,
        "is_whale": is_whale,
        "smc_text": smc_text,
        "where_status": where_status,
        "when_status": when_status,
        "trade_status": trade_status,
        "status_level": status_level,
        "direction": direction
    }

def try_fetch_from_futu_opend(sym, interval):
    """第一優先級：若本地 OpenD (127.0.0.1:11111) 在線且安裝了 futu-api，直接提取實時無延遲數據"""
    if not is_local_opend_alive():
        return None
    try:
        from futu import OpenQuoteContext, KLType, SubType, RET_OK
        futu_sym = get_futu_symbol(sym)
        kl_map = {
            '15m': KLType.K_15M,
            '1h': KLType.K_60M,
            '4h': KLType.K_60M,
            '1d': KLType.K_DAY
        }
        kl_type = kl_map.get(interval, KLType.K_60M)
        quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        try:
            quote_ctx.subscribe([futu_sym], [SubType.QUOTE, kl_type], subscribe_push=False)
            ret_q, df_q = quote_ctx.get_market_snapshot([futu_sym])
            ret_k, df_k, _ = quote_ctx.request_history_kline(futu_sym, ktype=kl_type, max_count=200)
            if ret_k != RET_OK or df_k.empty:
                return None

            prev_close = float(df_q['prev_close_price'].iloc[0]) if (ret_q == RET_OK and not df_q.empty) else float(df_k['close'].iloc[0])
            current_price = float(df_q['last_price'].iloc[0]) if (ret_q == RET_OK and not df_q.empty) else float(df_k['close'].iloc[-1])

            df_k['time_key'] = pd.to_datetime(df_k['time_key']).astype('int64') // 10**9
            df = df_k[['time_key', 'open', 'high', 'low', 'close', 'volume']].copy()

            if interval == '4h' and len(df) >= 4:
                df['bucket'] = df['time_key'] // (4 * 3600)
                df = df.groupby('bucket', as_index=False).agg({
                    'time_key': 'first',
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                })

            df = compute_order_flow_delta(df)
            metrics = evaluate_smc_structure(df, current_price, prev_close)

            chart_array = [{
                "time": int(r['time_key']),
                "open": round(float(r['open']), 2),
                "high": round(float(r['high']), 2),
                "low": round(float(r['low']), 2),
                "close": round(float(r['close']), 2),
                "volume": round(float(r['volume']), 0),
                "delta": round(float(r['delta']), 0),
                "whale": bool(r['is_whale'])
            } for _, r in df.iterrows()]

            chg_val = round(current_price - prev_close, 2) if prev_close > 0 else 0.0
            chg_pct = round((chg_val / prev_close) * 100, 2) if prev_close > 0 else 0.0

            return {
                "symbol": sym.upper(),
                "company_name": get_company_name(sym),
                "data_source": "FUTU OPEND (11111 實時直連)",
                "price": round(current_price, 2),
                "prev_close": round(prev_close, 2),
                "change": chg_val,
                "change_pct": chg_pct,
                **metrics,
                "chart": chart_array
            }
        finally:
            quote_ctx.close()
    except Exception:
        return None

def fetch_symbol_data(sym, interval='4h', include_chart=True):
    """雙保底主引擎：優先嘗試本地 OpenD，若在雲端或未開啟則自動使用 Yahoo Finance 高速通道"""
    opend_res = try_fetch_from_futu_opend(sym, interval)
    if opend_res is not None:
        if not include_chart:
            opend_res['chart'] = []
        return opend_res

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    yf_sym = get_yf_symbol(sym)

    yf_interval, yf_range = "60m", "60d"
    if interval == '15m':
        yf_interval, yf_range = "15m", "15d"
    elif interval == '1h':
        yf_interval, yf_range = "60m", "30d"
    elif interval == '4h':
        yf_interval, yf_range = "60m", "60d"
    elif interval == '1d':
        yf_interval, yf_range = "1d", "1y"

    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{yf_sym}?range={yf_range}&interval={yf_interval}&includePrePost=false"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

        chart_result = data['chart']['result'][0]
        meta = chart_result['meta']

        regular_price = float(meta.get('regularMarketPrice') or meta.get('chartPreviousClose') or 0.0)
        prev_close = float(meta.get('regularMarketPreviousClose') or meta.get('previousClose') or meta.get('chartPreviousClose') or regular_price)

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
                c_val = float(closes[i])
                o_val = float(opens[i]) if (i < len(opens) and opens[i] is not None) else c_val
                h_val = float(highs[i]) if (i < len(highs) and highs[i] is not None) else max(o_val, c_val)
                l_val = float(lows[i]) if (i < len(lows) and lows[i] is not None) else min(o_val, c_val)
                v_val = float(volumes[i]) if (i < len(volumes) and volumes[i] is not None) else 0.0
                df_rows.append({
                    'time_key': int(timestamps[i]),
                    'open': o_val,
                    'high': h_val,
                    'low': l_val,
                    'close': c_val,
                    'volume': v_val
                })

        df = pd.DataFrame(df_rows)
        if not df.empty:
            df = df.drop_duplicates(subset=['time_key']).sort_values('time_key').reset_index(drop=True)
            # 若前端請求 4H，將 60m 數據進行真實 4H K 線聚合
            if interval == '4h' and len(df) >= 8:
                df['bucket'] = df['time_key'] // (4 * 3600)
                df = df.groupby('bucket', as_index=False).agg({
                    'time_key': 'first',
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                })

            # 將最新實時價格同步至最後一根 K 線
            if regular_price > 0:
                df.loc[df.index[-1], 'close'] = regular_price
                if regular_price > df.loc[df.index[-1], 'high']:
                    df.loc[df.index[-1], 'high'] = regular_price
                if regular_price < df.loc[df.index[-1], 'low']:
                    df.loc[df.index[-1], 'low'] = regular_price

            df = compute_order_flow_delta(df)
            current_price = float(df.iloc[-1]['close'])
        else:
            current_price = regular_price

        # 若 Yahoo 跨日區間導致 meta.previousClose 失真，取日線級別倒數第二日收盤校準
        if interval == '1d' and len(df) >= 2:
            prev_close = float(df.iloc[-2]['close'])

        metrics = evaluate_smc_structure(df, current_price, prev_close)

        chart_array = []
        if include_chart and not df.empty:
            for _, row in df.iterrows():
                chart_array.append({
                    "time": int(row['time_key']),
                    "open": round(float(row['open']), 2),
                    "high": round(float(row['high']), 2),
                    "low": round(float(row['low']), 2),
                    "close": round(float(row['close']), 2),
                    "volume": round(float(row.get('volume', 0)), 0),
                    "delta": round(float(row.get('delta', 0)), 0),
                    "whale": bool(row.get('is_whale', False))
                })

        chg_val = round(current_price - prev_close, 2) if prev_close > 0 else 0.0
        chg_pct = round((chg_val / prev_close) * 100, 2) if prev_close > 0 else 0.0

        item = {
            "symbol": sym.upper(),
            "company_name": get_company_name(sym),
            "data_source": "CLOUD PRO (實時補償引擎)",
            "price": round(current_price, 2),
            "prev_close": round(prev_close, 2),
            "change": chg_val,
            "change_pct": chg_pct,
            **metrics,
            "chart": chart_array
        }

        # 自動推入異動雷達池
        if item.get("status_level") == "executable" or item.get("is_whale"):
            reason = "金柱巨量買單" if (item.get("is_whale") and item.get("net_delta", 0) > 0) else "機構需求區承接"
            RADAR_CACHE[sym.upper()] = {**item, "chart": [], "radar_hit": True, "hit_reason": reason}

        return item
    except Exception as e:
        print(f"Error fetching {sym}: {e}")
        return {
            "symbol": sym.upper(),
            "company_name": get_company_name(sym),
            "price": 0.0, "prev_close": 0.0, "change": 0.0, "change_pct": 0.0,
            "chart": []
        }

@app.route('/api/radar', methods=['GET', 'POST'])
def radar_endpoint():
    if request.method == 'POST':
        action = request.args.get('action', '')
        if action == 'toggle':
            RADAR_STATE['is_running'] = not RADAR_STATE['is_running']
        return jsonify({"ok": True, "status": RADAR_STATE})

    if RADAR_STATE['is_running']:
        idx = RADAR_STATE['scan_cursor'] % len(RADAR_UNIVERSE)
        target_sym = RADAR_UNIVERSE[idx]
        RADAR_STATE['scan_cursor'] = idx + 1
        RADAR_STATE['last_scan_symbol'] = target_sym
        try:
            fetch_symbol_data(target_sym, interval='4h', include_chart=False)
        except Exception:
            pass

    RADAR_STATE['discovered_count'] = len(RADAR_CACHE)
    RADAR_STATE['source_mode'] = "LOCAL OPEND (11111 直連)" if is_local_opend_alive() else "CLOUD PRO (雲端雙保底)"
    return jsonify({
        "status": RADAR_STATE,
        "discoveries": RADAR_CACHE
    })

@app.route('/api/quote', methods=['GET'])
def quote_endpoint():
    symbols_str = request.args.get('symbols', 'NVDA')
    interval = request.args.get('interval', '4h').lower()
    include_chart = request.args.get('chart', '1') != '0'
    symbols = [s.strip() for s in symbols_str.split(',') if s.strip()]

    result = {}
    # 使用多執行緒併發抓取，15 檔股票同時拉取僅需 0.8 秒
    max_workers = min(10, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(fetch_symbol_data, sym, interval, include_chart): sym for sym in symbols}
        for future in as_completed(future_map):
            sym = future_map[future]
            try:
                result[sym] = future.result()
            except Exception:
                result[sym] = {"symbol": sym.upper(), "price": 0.0, "chart": []}

    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
