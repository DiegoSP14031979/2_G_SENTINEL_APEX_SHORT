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
    """Evalúa la salud macro del SPY."""
    try:
        spy = yf.Ticker("SPY").history(period="6mo")
        if len(spy) < 50:
            return "NEUTRAL", 0.0
        
        spy['EMA50'] = spy['Close'].ewm(span=50, adjust=False).mean()
        precio_actual = spy['Close'].iloc[-1]
        ema50_actual = spy['EMA50'].iloc[-1]
        
        regimen = "BULLISH" if precio_actual > ema50_actual else "BEARISH"
        distancia_pct = ((precio_actual - ema50_actual) / ema50_actual) * 100
        return regimen, distancia_pct
    except Exception as e:
        print(f"⚠️ Error al calcular Filtro Macro SPY: {e}")
        return "NEUTRAL", 0.0

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
    
    regimen_macro, distancia_spy = obtener_regimen_macro_spy()
    
    decisiones_log = []
    decisiones_log.append(f"🔍 SCANNER 24/5 // MACRO SPY: {regimen_macro} ({distancia_spy:+.2f}% vs EMA50)")
    
    # 1. Monitoreo y actualización de slots activos
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
        rendimiento_pct = ((precio_entrada - precio_actual) / precio_entrada) * 100
        
        stop_loss = pos["stop_loss"]
        take_profit = pos["take_profit"]
        
        # Protecciones
        if rendimiento_pct >= 1.2 and stop_loss > precio_entrada:
            pos["stop_loss"] = precio_entrada
            decisiones_log.append(f"🛡️ Break-Even activado para SHORT {ticker} a {precio_entrada:.2f}")
            
        if rendimiento_pct >= 2.5:
            nuevo_sl = precio_actual * 1.008
            if nuevo_sl < stop_loss:
                pos["stop_loss"] = nuevo_sl
                decisiones_log.append(f"📈 Trailing Stop ajustado para SHORT {ticker} a {nuevo_sl:.2f}")
        
        # Cierres
        if precio_actual >= pos["stop_loss"]:
            pnl_eur = -RIESGO_MAX_SLOT
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "STOP_LOSS"
            })
            decisiones_log.append(f"❌ Cierre por SL en SHORT {ticker} | PnL: {pnl_eur:.2f} €")
        elif precio_actual <= take_profit:
            pnl_eur = RIESGO_MAX_SLOT * 2.2
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "TAKE_PROFIT"
            })
            decisiones_log.append(f"🎯 Cierre por TP en SHORT {ticker} | PnL: +{pnl_eur:.2f} €")
        else:
            slots_restantes.append(pos)
            
    # 2. Scanner y Evaluación de Oportunidades SHORT
    slots_disponibles = SLOTS_TOTALES - len(slots_restantes)
    
    for clave, datos in TICKERS_COBERTURA.items():
        symbol = datos["index"]
        
        # Omitir si ya está en cartera
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
            
            # Condiciones Cuantitativas Adaptativas para SHORT:
            # Opción A: Tendencia bajista local (Precio < EMA20 y EMA20 < EMA50)
            condicion_tendencia = (close < ema20) and (ema20 < ema50)
            
            # Opción B: Sobrecompra extrema (RSI > 68) - Oportunidad de Reversión
            condicion_sobrecompra = rsi > 68
            
            # Opción C: Ruptura del mínimo del día anterior
            low_prev = df['Low'].iloc[-2]
            condicion_ruptura = close < low_prev
            
            if slots_disponibles > 0 and (condicion_tendencia or condicion_sobrecompra or (condicion_ruptura and close < ema20)):
                sl_price = close + (atr * 1.8)
                tp_price = close - (atr * 2.8)
                
                motivo = "TENDENCIA_BAJISTA" if condicion_tendencia else ("SOBRECOMPRA_RSI" if condicion_sobrecompra else "RUPTURA_LOW")
                
                nuevo_slot = {
                    "symbol": symbol,
                    "label": datos["label"],
                    "side": "SHORT",
                    "entry_price": round(close, 2),
                    "stop_loss": round(sl_price, 2),
                    "take_profit": round(tp_price, 2),
                    "timestamp": timestamp_str,
                    "allocated_capital": CAPITAL_POR_SLOT,
                    "reason": motivo
                }
                slots_restantes.append(nuevo_slot)
                slots_disponibles -= 1
                decisiones_log.append(f"🚀 ENTRADA SHORT ACTIVADA: {datos['label']} ({symbol}) a {close:.2f} | Motivo: {motivo} | RSI: {rsi:.1f}")
            else:
                # Registrar telemetría de monitoreo activo
                estado_ind = "BAJISTA" if close < ema20 else "ALCISTA"
                decisiones_log.append(f"📊 Monitoreo {datos['label']}: {close:.2f} (Tendencia Local: {estado_ind} | RSI: {rsi:.1f}) -> Esperando gatillo SHORT")
                
        except Exception as e:
            decisiones_log.append(f"⚠️ Error al escanear {symbol}: {e}")

    # Guardar estado
    posiciones["slots_activos"] = slots_restantes
    posiciones["capital_libre"] = capital_acumulado
    posiciones["last_update"] = timestamp_str
    posiciones["macro_status"] = regimen_macro
    posiciones["decisiones_log"] = decisiones_log
    
    # Calcular métricas
    ops = historial.get("operaciones", [])
    if ops:
        ganadoras = [o for o in ops if o["pnl_eur"] > 0]
        perdedoras = [o for o in ops if o["pnl_eur"] <= 0]
        win_rate = (len(ganadoras) / len(ops)) * 100
        profit = sum(o["pnl_eur"] for o in ganadoras)
        loss = abs(sum(o["pnl_eur"] for o in perdedoras))
        profit_factor = round(profit / loss, 2) if loss > 0 else (profit if profit > 0 else 1.0)
        historial["metricas"] = {
            "win_rate": round(win_rate, 1),
            "profit_factor": profit_factor,
            "total_trades": len(ops),
            "trades_ganadores": len(ganadoras),
            "trades_perdedores": len(perdedoras)
        }

    guardar_json(POSICIONES_FILE, posiciones)
    guardar_json(HISTORIAL_FILE, historial)
    print(f"[{timestamp_str}] Sincronización G-CORE finalizada exitosamente.")

if __name__ == "__main__":
    ejecutar_motor_cuantitativo_short()
