import os
import json
import urllib.request
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# -------------------------------------------------------------------
# CONFIGURACIÓN G-CORE: PATA 4 (NEXUS SHORT // CRYPTO ENGINE V4.1)
# -------------------------------------------------------------------
CAPITAL_INICIAL = 3300.0
SLOTS_TOTALES = 4
CAPITAL_POR_SLOT = CAPITAL_INICIAL / SLOTS_TOTALES
RIESGO_BASE_SLOT = CAPITAL_INICIAL * 0.015  # 1.5% de riesgo base por trade

UNIVERSO_CRYPTO = {
    "bitcoin": {"symbol": "BTC", "label": "Bitcoin"},
    "ethereum": {"symbol": "ETH", "label": "Ethereum"},
    "solana": {"symbol": "SOL", "label": "Solana"},
    "ripple": {"symbol": "XRP", "label": "Ripple"},
    "dogecoin": {"symbol": "DOGE", "label": "Dogecoin"}
}

POSICIONES_FILE = "posiciones.json"
HISTORIAL_FILE = "historial.json"
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def cargar_json(filename, default_data):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default_data
    return default_data

def guardar_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_coin_df(coin_id, days=30):
    """Obtiene velas horarias ajustadas de CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days={days}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode('utf-8'))
            prices = data.get("prices", [])
            if len(prices) < 50:
                return None
            
            # Muestreo cada 12 registros de 5 min para simular velas horarias limpia
            prices_sampled = prices[::12] if len(prices) > 300 else prices
            df = pd.DataFrame(prices_sampled, columns=['timestamp', 'Close'])
            # Estimación sintética de High/Low basada en volatilidad local para ATR
            df['High'] = df['Close'] * 1.002
            df['Low'] = df['Close'] * 0.998
            return df
    except Exception as e:
        print(f"[ERROR DATA {coin_id}]: {e}")
        return None

def obtener_regimen_macro_btc():
    """Evalúa la tendencia dominante de Bitcoin como filtro macro global"""
    df_btc = get_coin_df("bitcoin", days=60)
    if df_btc is None or len(df_btc) < 50:
        return "NEUTRAL", 0.0, 1.0

    df_btc['EMA20'] = df_btc['Close'].ewm(span=20, adjust=False).mean()
    df_btc['EMA50'] = df_btc['Close'].ewm(span=50, adjust=False).mean()
    
    precio = df_btc['Close'].iloc[-1]
    ema20 = df_btc['EMA20'].iloc[-1]
    ema50 = df_btc['EMA50'].iloc[-1]
    
    distancia_pct = ((precio - ema50) / ema50) * 100
    
    if precio < ema20 and ema20 < ema50:
        regimen = "STRONG_BEARISH_CRYPTO"
        sizing_factor = 1.0  # 100% de riesgo asignado a shorts
    elif precio < ema20 or precio < ema50:
        regimen = "WEAK_BEARISH_CRYPTO"
        sizing_factor = 0.75  # 75% de riesgo
    else:
        regimen = "BULLISH_TREND_CRYPTO"
        sizing_factor = 0.5   # Reducir exposición en corto si BTC está alcista
        
    return regimen, distancia_pct, sizing_factor

def calcular_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calcular_atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def ejecutar_motor_cuantitativo_short_crypto():
    now_utc = datetime.now(timezone.utc)
    timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    posiciones = cargar_json(POSICIONES_FILE, {"slots_activos": [], "capital_libre": CAPITAL_INICIAL})
    historial = cargar_json(HISTORIAL_FILE, {"operaciones": [], "metricas": {"win_rate": 0.0, "profit_factor": 1.0}})
    
    regimen_macro, distancia_btc, sizing_factor = obtener_regimen_macro_btc()
    riesgo_actual_slot = RIESGO_BASE_SLOT * sizing_factor
    
    decisiones_log = []
    decisiones_log.append(f"⚡ G-CORE NEXUS SHORT V4.1 // MACRO BTC: {regimen_macro} | Risk Factor: {sizing_factor*100:.0f}% (${riesgo_actual_slot:.2f})")
    
    slots_restantes = []
    capital_acumulado = posiciones.get("capital_libre", CAPITAL_INICIAL)
    
    # 1. MONITOREO DE POSICIONES CORTAS ACTIVAS
    for pos in posiciones.get("slots_activos", []):
        coin_id = pos["coin_id"]
        df = get_coin_df(coin_id, days=5)
        if df is None or df.empty:
            slots_restantes.append(pos)
            continue
            
        precio_actual = df['Close'].iloc[-1]
        precio_entrada = pos["entry_price"]
        
        # Rendimiento en SHORT: Ganamos si el precio cae
        rendimiento_pct = ((precio_entrada - precio_actual) / precio_entrada) * 100
        stop_loss = pos["stop_loss"]
        take_profit = pos["take_profit"]
        
        # Corrección de seguridad SHORT: SL debe estar por encima de entrada
        if stop_loss <= precio_entrada:
            stop_loss = round(precio_entrada * 1.02, 4)
            pos["stop_loss"] = stop_loss
        
        # Break-Even dinámico: Proteger a la entrada tras +1.0% de ganancia
        if rendimiento_pct >= 1.0 and stop_loss > precio_entrada:
            pos["stop_loss"] = precio_entrada
            decisiones_log.append(f"🛡️ Break-Even activado para SHORT {pos['symbol']} a ${precio_entrada:.4f}")
            
        # Evaluaciones de Cierre SHORT
        if precio_actual >= pos["stop_loss"]:
            pnl_usd = -pos.get("risk_allocated", RIESGO_BASE_SLOT)
            capital_acumulado += pnl_usd
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": pos['symbol'], "side": "SHORT",
                "pnl_usd": round(pnl_usd, 2), "reason": "STOP_LOSS"
            })
            decisiones_log.append(f"❌ SL CERRADO en SHORT {pos['symbol']} | PnL: ${pnl_usd:.2f}")
            
        elif precio_actual <= take_profit:
            pnl_usd = pos.get("risk_allocated", RIESGO_BASE_SLOT) * 2.2
            capital_acumulado += pnl_usd
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": pos['symbol'], "side": "SHORT",
                "pnl_usd": round(pnl_usd, 2), "reason": "TAKE_PROFIT"
            })
            decisiones_log.append(f"🎯 TP CERRADO en SHORT {pos['symbol']} | PnL: +${pnl_usd:.2f}")
        else:
            pos["precio_actual"] = round(precio_actual, 4)
            slots_restantes.append(pos)
            
    # 2. SCANNER DE NUEVAS OPORTUNIDADES EN CORTO
    slots_disponibles = SLOTS_TOTALES - len(slots_restantes)
    
    for coin_id, datos in UNIVERSO_CRYPTO.items():
        if any(p.get("coin_id") == coin_id for p in slots_restantes):
            continue
            
        df = get_coin_df(coin_id, days=30)
        if df is None or len(df) < 30:
            continue
            
        close = df['Close'].iloc[-1]
        ema20 = df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
        rsi_series = calcular_rsi(df['Close'])
        rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50
        atr = calcular_atr(df)
        
        # Disparadores Bajistas Cuantitativos
        gatillo_a = (close < ema20) and (ema20 < ema50)  # Tendencia bajista confirmada
        gatillo_b = (rsi > 65)                            # Sobreclave / Exhaustión compradora
        gatillo_c = (close < ema20)                       # Debilidad de corto plazo
        
        if slots_disponibles > 0 and (gatillo_a or gatillo_b or gatillo_c):
            motivo = "BREAKOUT_BEARISH" if gatillo_a else ("EXHAUSTION_REVERSAL" if gatillo_b else "LOCAL_WEAKNESS")
            
            # FÓRMULA BLINDADA SHORT CRYPTO: SL por arriba (+), TP por abajo (-)
            sl_price = round(close + (atr * 1.5), 4)
            tp_price = round(close - (atr * 1.5 * 2.2), 4)
            
            nuevo_slot = {
                "coin_id": coin_id,
                "symbol": datos["symbol"],
                "label": datos["label"],
                "side": "SHORT",
                "entry_price": round(close, 4),
                "precio_actual": round(close, 4),
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "timestamp": timestamp_str,
                "allocated_capital": CAPITAL_POR_SLOT,
                "risk_allocated": round(riesgo_actual_slot, 2),
                "trigger_type": motivo,
                "rsi": round(rsi, 1)
            }
            slots_restantes.append(nuevo_slot)
            slots_disponibles -= 1
            decisiones_log.append(f"🚀 SHORT ENTRY: {datos['symbol']} a ${close:.4f} | SL: ${sl_price:.4f} | TP: ${tp_price:.4f}")

    posiciones["slots_activos"] = slots_restantes
    posiciones["capital_libre"] = round(capital_acumulado, 2)
    posiciones["last_update"] = timestamp_str
    posiciones["macro_status"] = regimen_macro
    posiciones["decisiones_log"] = decisiones_log

    guardar_json(POSICIONES_FILE, posiciones)
    guardar_json(HISTORIAL_FILE, historial)
    print(" -> Engine NEXUS SHORT ejecutado correctamente.")

if __name__ == "__main__":
    ejecutar_motor_cuantitativo_short_crypto()
