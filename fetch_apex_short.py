import os
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# -------------------------------------------------------------------
# CONFIGURACIÓN G-CORE: PATA 2 (APEX SHORT // TRADITIONAL MARKETS)
# -------------------------------------------------------------------
CAPITAL_INICIAL = 3000.0
SLOTS_TOTALES = 4
CAPITAL_POR_SLOT = CAPITAL_INICIAL / SLOTS_TOTALES  # 750.0 €
RIESGO_MAX_SLOT = CAPITAL_INICIAL * 0.015  # 45.0 € (1.5%)

TICKERS_COBERTURA = {
    "ASIA": {"index": "^N225", "forex": "USDJPY=X", "label": "Nikkei 225 / USDJPY"},
    "EUROPE": {"index": "^GDAXI", "forex": "EURUSD=X", "label": "DAX 40 / EURUSD"},
    "US": {"index": "^GSPC", "forex": "GBPUSD=X", "label": "S&P 500 / GBPUSD"}
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
    """Evalúa si el mercado global (SPY) está en régimen alcista o bajista."""
    try:
        spy = yf.Ticker("SPY").history(period="1y")
        if len(spy) < 200:
            return "NEUTRAL", 0.0
        
        spy['EMA200'] = spy['Close'].ewm(span=200, adjust=False).mean()
        precio_actual = spy['Close'].iloc[-1]
        ema200_actual = spy['EMA200'].iloc[-1]
        
        # Régimen SHORT válido si SPY está por debajo de EMA200
        regimen = "BEARISH" if precio_actual < ema200_actual else "BULLISH"
        distancia_pct = ((precio_actual - ema200_actual) / ema200_actual) * 100
        return regimen, distancia_pct
    except Exception as e:
        print(f"⚠️ Error al calcular Filtro Macro SPY: {e}")
        return "NEUTRAL", 0.0

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
    historial = cargar_json(HISTORIAL_FILE, {"operaciones": [], "metricas": {"win_rate": 0.0, "profit_factor": 0.0}})
    
    regimen_macro, distancia_spy = obtener_regimen_macro_spy()
    print(f"[{timestamp_str}] Filtro Macro SPY: {regimen_macro} ({distancia_spy:.2f}%)")
    
    decisiones_log = []
    
    # 1. Monitoreo y actualización de slots activos (Trailing Stop / BE)
    slots_restantes = []
    capital_acumulado = posiciones.get("capital_libre", CAPITAL_INICIAL)
    
    for pos in posiciones.get("slots_activos", []):
        ticker = pos["symbol"]
        ticker_data = yf.Ticker(ticker).history(period="5d", interval="1h")
        if ticker_data.empty:
            slots_restantes.append(pos)
            continue
            
        precio_actual = ticker_data['Close'].iloc[-1]
        precio_entrada = pos["entry_price"]
        
        # En SHORT, la ganancia ocurre cuando el precio cae
        rendimiento_pct = ((precio_entrada - precio_actual) / precio_entrada) * 100
        
        # Lógica Trailing Stop / Break-Even
        stop_loss = pos["stop_loss"]
        take_profit = pos["take_profit"]
        
        # Protección a Break-Even (+1.5% de beneficio)
        if rendimiento_pct >= 1.5 and stop_loss > precio_entrada:
            pos["stop_loss"] = precio_entrada
            decisiones_log.append(f"🛡️ Break-Even activado para SHORT {ticker} a {precio_entrada:.2f}")
            
        # Trailing Stop dinámico (+3.0% de beneficio)
        if rendimiento_pct >= 3.0:
            nuevo_sl = precio_actual * 1.01  # Trailing a 1% de distancia por encima del precio
            if nuevo_sl < stop_loss:
                pos["stop_loss"] = nuevo_sl
                decisiones_log.append(f"📈 Trailing Stop actualizado para SHORT {ticker} a {nuevo_sl:.2f}")
        
        # Verificación de Cierre (SL o TP)
        if precio_actual >= pos["stop_loss"]:
            pnl_eur = -RIESGO_MAX_SLOT
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "STOP_LOSS"
            })
            decisiones_log.append(f"❌ Cierre por SL en SHORT {ticker} | PnL: {pnl_eur:.2f} €")
        elif precio_actual <= take_profit:
            pnl_eur = RIESGO_MAX_SLOT * 2.5  # Captura ratio riesgo-beneficio
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "TAKE_PROFIT"
            })
            decisiones_log.append(f"🎯 Cierre por TP en SHORT {ticker} | PnL: +{pnl_eur:.2f} €")
        else:
            slots_restantes.append(pos)
            
    # 2. Evaluación de Nuevas Entradas (Si hay slots disponibles y Filtro Macro es Bajista/Neutral)
    slots_disponibles = SLOTS_TOTALES - len(slots_restantes)
    
    if slots_disponibles > 0 and regimen_macro != "BULLISH":
        # Evaluar activos según la sesión
        for sesion, datos in TICKERS_COBERTURA.items():
            if slots_disponibles <= 0:
                break
                
            symbol = datos["index"]
            # Evitar duplicados
            if any(p["symbol"] == symbol for p in slots_restantes):
                continue
                
            df = yf.Ticker(symbol).history(period="1 mo", interval="1d")
            if len(df) < 20:
                continue
                
            close = df['Close'].iloc[-1]
            ema50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
            atr = calcular_atr(df)
            
            # Condición de Entrada SHORT: Precio bajo EMA50 con momentum bajista
            if close < ema50:
                mult_sl = 2.0 if atr > df['Close'].std() else 1.5
                mult_tp = 3.5 if atr > df['Close'].std() else 3.0
                
                sl_price = close + (atr * mult_sl)
                tp_price = close - (atr * mult_tp)
                
                nuevo_slot = {
                    "symbol": symbol,
                    "label": datos["label"],
                    "side": "SHORT",
                    "entry_price": round(close, 2),
                    "stop_loss": round(sl_price, 2),
                    "take_profit": round(tp_price, 2),
                    "timestamp": timestamp_str,
                    "allocated_capital": CAPITAL_POR_SLOT
                }
                slots_restantes.append(nuevo_slot)
                slots_disponibles -= 1
                decisiones_log.append(f"🚀 Entrada SHORT ejecutada en {datos['label']} ({symbol}) a {close:.2f}")

    # Guardar estado actualizado
    posiciones["slots_activos"] = slots_restantes
    posiciones["capital_libre"] = capital_acumulado
    posiciones["last_update"] = timestamp_str
    posiciones["macro_status"] = regimen_macro
    posiciones["decisiones_log"] = decisiones_log
    
    # Calcular métricas del historial
    ops = historial.get("operaciones", [])
    if ops:
        ganadoras = [o for o in ops if o["pnl_eur"] > 0]
        perdedoras = [o for o in ops if o["pnl_eur"] <= 0]
        win_rate = (len(ganadoras) / len(ops)) * 100
        profit = sum(o["pnl_eur"] for o in ganadoras)
        loss = abs(sum(o["pnl_eur"] for o in perdedoras))
        profit_factor = round(profit / loss, 2) if loss > 0 else (profit if profit > 0 else 1.0)
        historial["metricas"] = {"win_rate": round(win_rate, 1), "profit_factor": profit_factor}

    guardar_json(POSICIONES_FILE, posiciones)
    guardar_json(HISTORIAL_FILE, historial)
    print(f"[{timestamp_str}] Sincronización G-CORE completada exitosamente.")

if __name__ == "__main__":
    ejecutar_motor_cuantitativo_short()
