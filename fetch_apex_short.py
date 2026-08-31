import os
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# -------------------------------------------------------------------
# CONFIGURACIÓN G-CORE: PATA 2 (APEX SHORT // ADAPTIVE ENGINE V4)
# -------------------------------------------------------------------
CAPITAL_INICIAL = 3000.0
SLOTS_TOTALES = 4
CAPITAL_POR_SLOT = CAPITAL_INICIAL / SLOTS_TOTALES  # 750.0 €
RIESGO_BASE_SLOT = CAPITAL_INICIAL * 0.015  # 45.0 € (1.5%)

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
    """Capa 1: Filtro Macro Multiescala Adaptativo."""
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
        
        # Evaluación de régimen y factor de sizing adaptativo
        if precio < ema20 and ema20 < ema50:
            regimen = "STRONG_BEARISH"
            sizing_factor = 1.0  # Risk 100% (1.5%)
        elif precio < ema20 or precio < ema50:
            regimen = "WEAK_BEARISH"
            sizing_factor = 0.75  # Risk 75%
        else:
            regimen = "BULLISH_OVEREXTENDED" if distancia_pct > 2.0 else "BULLISH_TREND"
            sizing_factor = 0.5   # Cobertura táctica con riesgo reducido (0.75%)
            
        return regimen, distancia_pct, sizing_factor
    except Exception as e:
        print(f"⚠️ Error al calcular Capa Macro SPY: {e}")
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
    decisiones_log.append(f"⚡ G-CORE ADAPTIVE ENGINE V4 // MACRO: {regimen_macro} ({distancia_spy:+.2f}% vs EMA50) | Factor Risk: {sizing_factor*100:.0f}% ({riesgo_actual_slot:.2f}€)")
    
    # -------------------------------------------------------------------
    # CAPA 4: GESTIÓN DE SLOTS ACTIVOS (BREAK-EVEN Y TRAILING STOP)
    # -------------------------------------------------------------------
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
        
        # Protecciones adaptativas de la Pata 4
        if rendimiento_pct >= 1.0 and stop_loss > precio_entrada:
            pos["stop_loss"] = precio_entrada
            decisiones_log.append(f"🛡️ CAPA 4: Break-Even activado para SHORT {ticker} a {precio_entrada:.2f}")
            
        if rendimiento_pct >= 2.0:
            nuevo_sl = precio_actual * 1.005  # Trailing ultra-ajustado al 0.5%
            if nuevo_sl < stop_loss:
                pos["stop_loss"] = nuevo_sl
                decisiones_log.append(f"📈 CAPA 4: Trailing Stop Dinámico ajustado en SHORT {ticker} a {nuevo_sl:.2f}")
        
        # Cierres
        if precio_actual >= pos["stop_loss"]:
            pnl_eur = -pos.get("risk_allocated", RIESGO_BASE_SLOT)
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "STOP_LOSS"
            })
            decisiones_log.append(f"❌ Cierre por SL en SHORT {ticker} | PnL: {pnl_eur:.2f} €")
        elif precio_actual <= take_profit:
            pnl_eur = pos.get("risk_allocated", RIESGO_BASE_SLOT) * 2.5
            capital_acumulado += pnl_eur
            historial["operaciones"].append({
                "timestamp": timestamp_str, "symbol": ticker, "side": "SHORT",
                "pnl_eur": pnl_eur, "reason": "TAKE_PROFIT"
            })
            decisiones_log.append(f"🎯 Cierre por TP en SHORT {ticker} | PnL: +{pnl_eur:.2f} €")
        else:
            slots_restantes.append(pos)
            
    # -------------------------------------------------------------------
    # CAPA 3: MATRIZ DE GATILLOS ADAPTATIVOS
    # -------------------------------------------------------------------
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
            high = df['High'].iloc[-1]
            low = df['Low'].iloc[-1]
            ema20 = df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
            ema50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
            rsi_series = calcular_rsi(df['Close'])
            rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50
            atr = calcular_atr(df)
            
            # Capa 2: Volatilidad y Multiplicadores Adaptativos ATR
            std_atr = df['Close'].pct_change().std() * close
            es_alta_volatilidad = atr > std_atr
            mult_sl = 2.2 if es_alta_volatilidad else 1.5
            mult_tp = 4.0 if es_alta_volatilidad else 2.8

            # Evaluador de Gatillos Pata 4:
            # Gatillo A: Tendencia Bajista Confirmada (Cierre < EMA20 < EMA50)
            gatillo_a = (close < ema20) and (ema20 < ema50)
            
            # Gatillo B: Agotamiento Alcista / Reversión (RSI > 60 con sombra superior de rechazo)
            sombra_superior = high - max(close, df['Open'].iloc[-1])
            cuerpo_vela = abs(close - df['Open'].iloc[-1])
            gatillo_b = (rsi > 60) and (sombra_superior > cuerpo_vela * 1.2)
            
            # Gatillo C: Cobertura Asimétrica de Debilidad Relativa (El índice cae en su sesión local aunque la Macro sea alcista)
            rendimiento_diario = ((close - df['Open'].iloc[-1]) / df['Open'].iloc[-1]) * 100
            gatillo_c = (rendimiento_diario < -0.3) and (close < ema20)
            
            if slots_disponibles > 0 and (gatillo_a or gatillo_b or gatillo_c):
                motivo = "BREAKOUT_BEARISH" if gatillo_a else ("EXHAUSTION_REVERSAL" if gatillo_b else "LOCAL_WEAKNESS_COVER")
                
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
                    "allocated_capital": CAPITAL_POR_SLOT,
                    "risk_allocated": round(riesgo_actual_slot, 2),
                    "trigger_type": motivo,
                    "rsi": round(rsi, 1)
                }
                slots_restantes.append(nuevo_slot)
                slots_disponibles -= 1
                decisiones_log.append(f"🚀 G-CORE TRIGGER ACTIVADO: {datos['label']} ({symbol}) a {close:.2f} | Tipo: {motivo} | Risk: {riesgo_actual_slot:.2f}€ | SL: {sl_price:.2f} | TP: {tp_price:.2f}")
            else:
                decisiones_log.append(f"📊 Capa Adaptativa [{datos['label']}]: {close:.2f} (RSI: {rsi:.1f} | Rend.Día: {rendimiento_diario:+.2f}%) -> Sin confluencia de gatillos")
                
        except Exception as e:
            decisiones_log.append(f"⚠️ Error en Capa Adaptativa ({symbol}): {e}")

    # Sincronización de estado JSON
    posiciones["slots_activos"] = slots_restantes
    posiciones["capital_libre"] = capital_acumulado
    posiciones["last_update"] = timestamp_str
    posiciones["macro_status"] = regimen_macro
    posiciones["decisiones_log"] = decisiones_log
    
    # Métricas
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
    print(f"[{timestamp_str}] Sincronización G-CORE Adaptativa V4 completada.")

if __name__ == "__main__":
    ejecutar_motor_cuantitativo_short()
