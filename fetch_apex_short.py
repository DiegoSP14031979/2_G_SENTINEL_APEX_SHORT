import os
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# -------------------------------------------------------------------
# CONFIGURACIÓN G-CORE: PATA 2 (APEX SHORT // ADAPTIVE ENGINE V4.1)
# -------------------------------------------------------------------
CAPITAL_INICIAL = 3000.0
SLOTS_TOTALES = 4
CAPITAL_POR_SLOT = CAPITAL_INICIAL / SLOTS_TOTALES
RIESGO_BASE_SLOT = CAPITAL_INICIAL * 0.015

TICKERS_COBERTURA = {
    "ASIA": {"index": "^N225", "forex": "USDJPY=X", "label": "Nikkei 225"},
    "EUROPE": {"index": "^GDAXI", "forex": "EURUSD=X", "label": "DAX 40"},
    "US_NDX": {"index": "^NDX", "forex": "GBPUSD=X", "label": "Nasdaq 100"},
    "US_SPX": {"index": "^GSPC", "forex": "EURUSD=X", "label": "S&P 500"}
}

POSICIONES_FILE = "posiciones_short.json"
HISTORIAL_FILE = "historial_short.json"

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

def obtener_regimen_macro_spy():
    try:
        spy = yf.Ticker("SPY").history(period="6mo")
        if len(spy) < 50:
            return "NEUTRAL", 0.0, 1.0
        
        spy['EMA20'] = spy['Close'].ewm(span=20, adjust=False).mean()
        spy['EMA50'] = spy['Close'].ewm(span=50, adjust=False).mean()
        precio = spy['Close'].iloc[-1]
        ema20 = spy['EMA20'].iloc[-1]
        ema50 = spy['EMA50'].iloc[-1]
        
        distancia_pct = ((precio - ema50) / ema50) * 100
        
        if precio < ema20 and ema20 < ema50:
            regimen = "STRONG_BEARISH"
            sizing_factor = 1.0
        elif precio < ema20 or precio < ema50:
            regimen = "WEAK_BEARISH"
            sizing_factor = 0.75
        else:
            regimen = "BULLISH_TREND"
            sizing_factor = 0.5
            
        return regimen, distancia_pct, sizing_factor
    except Exception as e:
        return "NEUTRAL", 0.0, 1.0

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

def ejecutar_motor_cuantitativo_short():
    now_utc = datetime.now(timezone.utc)
    timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    posiciones = cargar_json(POSICIONES_FILE, {"slots_activos": [], "capital_libre": CAPITAL_INICIAL})
    historial = cargar_json(HISTORIAL_FILE, {"operaciones": [], "metricas": {"win_rate": 0.0, "profit_factor": 1.0}})
    
    regimen_macro, distancia_spy, sizing_factor = obtener_regimen_macro_spy()
    riesgo_actual_slot = RIESGO_BASE_SLOT * sizing_factor
    
    decisiones_log = []
    decisiones_log.append(f"⚡ G-CORE ADAPTIVE ENGINE V4.1 // MACRO: {regimen_macro} | Factor Risk: {sizing_factor*100:.0f}% ({riesgo_actual_slot:.2f}€)")
    
    slots_restantes = []
    capital_acumulado = posiciones.get("capital_libre", CAPITAL_INICIAL)
    
    # 1. Monitoreo de Posiciones Activas
    for pos in posiciones.get("slots_activos", []):
        ticker = pos["symbol"]
        ticker_data = yf.Ticker(ticker).history(period="5d", interval="1h")
        if ticker_data.empty:
            slots_restantes.append(pos)
            continue
            
        precio_actual = ticker_data['Close'].iloc[-1]
        precio_entrada = pos["entry_price"]
        rendimiento_pct = ((precio_entrada - precio_actual) / precio_entrada) * 100
        
        stop_loss = pos["stop_loss"]
        take_profit = pos["take_profit"]
        
        # Corrección de seguridad: Garantizar SL por encima de entrada para SHORT
        if stop_loss <= precio_entrada:
            atr_temp = precio_entrada * 0.015
            stop_loss = round(precio_entrada + atr_temp, 2)
            pos["stop_loss"] = stop_loss
        
        # Protecciones
        if rendimiento_pct >= 1.0 and stop_loss > precio_entrada:
            pos["stop_loss"] = precio_entrada
            decisiones_log.append(f"🛡️ Break-Even activado para SHORT {ticker} a {precio_entrada:.2f}")
            
        # Cierres (SHORT: Perder es si el precio sube por encima del SL)
        if precio_actual >= pos["stop_loss"]:
            pnl_eur = -pos.get("risk_allocated", RIESGO_BASE_SLOT)
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "STOP_LOSS"
            })
            decisiones_log.append(f"❌ Cierre SL en SHORT {ticker} | PnL: {pnl_eur:.2f} €")
        elif precio_actual <= take_profit:
            pnl_eur = pos.get("risk_allocated", RIESGO_BASE_SLOT) * 2.5
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "TAKE_PROFIT"
            })
            decisiones_log.append(f"🎯 Cierre TP en SHORT {ticker} | PnL: +{pnl_eur:.2f} €")
        else:
            slots_restantes.append(pos)
            
    # 2. Scanner de Nuevas Oportunidades
    slots_disponibles = SLOTS_TOTALES - len(slots_restantes)
    
    for clave, datos in TICKERS_COBERTURA.items():
        symbol = datos["index"]
        
        if any(p["symbol"] == symbol for p in slots_restantes):
            continue
            
        try:
            df = yf.Ticker(symbol).history(period="2mo", interval="1d")
            if len(df) < 30:
                continue
                
            close = df['Close'].iloc[-1]
            ema20 = df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
            ema50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
            rsi_series = calcular_rsi(df['Close'])
            rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50
            atr = calcular_atr(df)
            
            gatillo_a = (close < ema20) and (ema20 < ema50)
            gatillo_b = (rsi > 60)
            gatillo_c = (close < ema20)
            
            if slots_disponibles > 0 and (gatillo_a or gatillo_b or gatillo_c):
                motivo = "BREAKOUT_BEARISH" if gatillo_a else ("EXHAUSTION_REVERSAL" if gatillo_b else "LOCAL_WEAKNESS_COVER")
                
                # FÓRMULA BLINDADA SHORT: Stop Loss ARRIBA (+), Take Profit ABAJO (-)
                sl_price = round(close + (atr * 2.0), 2)
                tp_price = round(close - (atr * 3.0), 2)
                
                nuevo_slot = {
                    "symbol": symbol,
                    "label": datos["label"],
                    "side": "SHORT",
                    "entry_price": round(close, 2),
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
                decisiones_log.append(f"🚀 TRIGGER SHORT: {datos['label']} a {close:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}")
        except Exception as e:
            decisiones_log.append(f"⚠️ Error escaneando {symbol}: {e}")

    # Resetear posiciones mal calculadas anteriores
    for s in slots_restantes:
        if s["stop_loss"] <= s["entry_price"]:
            s["stop_loss"] = round(s["entry_price"] * 1.015, 2)

    posiciones["slots_activos"] = slots_restantes
    posiciones["capital_libre"] = capital_acumulado
    posiciones["last_update"] = timestamp_str
    posiciones["macro_status"] = regimen_macro
    posiciones["decisiones_log"] = decisiones_log

    guardar_json(POSICIONES_FILE, posiciones)
    guardar_json(HISTORIAL_FILE, historial)

if __name__ == "__main__":
    ejecutar_motor_cuantitativo_short()
