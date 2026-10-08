"""Mesa cripto de GSAM Capital · BTC y ETH.

Siete agentes con mentalidad de fondo cripto institucional preparan el brief dos veces al día:
  macro        → dólar, tasas y Nasdaq (el apetito de riesgo que mueve a cripto)
  flujos       → stablecoins (dinero nuevo entrando o saliendo) y dominancia de BTC
  sentimiento  → Fear & Greed y funding de perpetuos (¿quién está apalancado?)
  liquidez     → niveles: máximos/mínimos de la semana y del mes, EMA200 diaria, valor justo
  historiador  → estacionalidad de BTC en los últimos años y años análogos
  riesgo       → volatilidad (ATR), régimen y tamaño de posición
  cio          → junta todo y escribe el brief con sesgo, zonas y escenarios
  ejecucion    → (opcional) compra en Blockchain.com Exchange con orden límite, riesgo 1% y stop protegiendo

Uso: python agentes/cripto.py <manana|noche|vigilar>
Todo queda en data/cripto.json (la página cripto.html lo lee en vivo).
Es análisis educativo; no es consejo financiero. Solo ejecuta si MODO_CRIPTO=real.
"""
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main as base  # Gemini, ntfy y git
import mesa as M     # datos de Yahoo, ATR, EMA e historiador

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / "data" / "cripto.json"
UA = {"User-Agent": "Mozilla/5.0 (GSAM Cripto)"}
ACTIVOS = {"btc": "BTC-USD", "eth": "ETH-USD"}
MACRO = {"dxy": "DX-Y.NYB", "us10y": "^TNX", "nasdaq": "NQ=F", "vix": "^VIX"}
TURNOS = {"manana": "Mañana (apertura de EE. UU.)", "noche": "Noche (Asia)"}


class Mesa:
    def __init__(self):
        if base.EN_ACTIONS:
            base.git("pull", "--rebase", "--quiet", check=False)
        try:
            self.m = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        except Exception:
            self.m = {"agentes": {}, "actividad": [], "briefs": []}

    def guardar(self, msg):
        self.m["actividad"] = self.m["actividad"][:150]
        self.m["briefs"] = self.m["briefs"][:40]
        ARCHIVO.write_text(json.dumps(self.m, ensure_ascii=False, indent=1), encoding="utf-8")
        if not base.EN_ACTIONS:
            return
        base.git("add", "data/cripto.json")
        if base.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        base.git("commit", "-q", "-m", f"[cripto] {msg[:60]}")
        for i in range(4):
            if base.git("push", "-q", check=False).returncode == 0:
                return
            base.git("pull", "--rebase", "-X", "theirs", "--quiet", check=False)
            time.sleep(2 + 2 * i)

    def paso(self, agente, texto, trabajando=True):
        print(f"[{agente}] {texto}")
        self.m["actividad"].insert(0, {"agente": agente, "texto": texto, "ts": base.ahora()})
        a = self.m["agentes"].setdefault(agente, {})
        if trabajando:
            a.update(estado="trabajando", tarea=texto, actualizado=base.ahora())
        else:
            a.update(estado="descansando", tarea="", ultimo_resultado=texto, actualizado=base.ahora())
        self.guardar(texto)


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


# ───────── los agentes ─────────

def mapa(simbolo):
    d = M.yahoo(simbolo, "1d", "1y")
    h = M.yahoo(simbolo, "1h", "5d")
    if len(d) < 60:
        return {"error": "pocos datos"}
    c = [v["c"] for v in d]
    ult = d[-1]
    sem = d[-7:]
    mes = d[-30:]
    a = M.atr(d, 14)
    return {
        "precio": round(c[-1], 2),
        "cambio_24h_pct": _pct(c[-1], c[-2]),
        "cambio_7d_pct": _pct(c[-1], c[-8]),
        "cambio_30d_pct": _pct(c[-1], c[-31]),
        "max_ayer": round(d[-2]["h"], 2), "min_ayer": round(d[-2]["l"], 2),
        "max_semana": round(max(v["h"] for v in sem), 2), "min_semana": round(min(v["l"] for v in sem), 2),
        "max_mes": round(max(v["h"] for v in mes), 2), "min_mes": round(min(v["l"] for v in mes), 2),
        "ema50_diaria": round(M.ema(c, 50), 2), "ema200_diaria": round(M.ema(c, 200), 2),
        "atr_diario": round(a, 2) if a else None,
        "atr_pct": round(a / c[-1] * 100, 2) if a else None,
        "max_48h": round(max(v["h"] for v in h[-48:]), 2) if h else None,
        "min_48h": round(min(v["l"] for v in h[-48:]), 2) if h else None,
        "rango_hoy_vs_atr_pct": round((ult["h"] - ult["l"]) / a * 100) if a else None,
    }


def macro():
    out = {}
    for k, s in MACRO.items():
        try:
            d = M.yahoo(s, "1d", "1mo")
            out[k] = {"ultimo": round(d[-1]["c"], 2), "cambio_1d_pct": _pct(d[-1]["c"], d[-2]["c"]), "cambio_5d_pct": _pct(d[-1]["c"], d[-6]["c"])}
        except Exception as ex:
            out[k] = {"error": str(ex)[:60]}
    return out


def flujos():
    out = {}
    try:
        g = _get("https://api.coingecko.com/api/v3/global")["data"]
        out["dominancia_btc_pct"] = round(g["market_cap_percentage"]["btc"], 2)
        out["dominancia_eth_pct"] = round(g["market_cap_percentage"]["eth"], 2)
        out["cap_total_cambio_24h_pct"] = round(g.get("market_cap_change_percentage_24h_usd", 0), 2)
    except Exception as ex:
        out["global_error"] = str(ex)[:60]
    try:
        st = _get("https://stablecoins.llama.fi/stablecoins?includePrices=false")["peggedAssets"]
        top = sorted(st, key=lambda x: (x.get("circulating") or {}).get("peggedUSD") or 0, reverse=True)[:10]
        f = lambda k: sum((x.get(k) or {}).get("peggedUSD") or 0 for x in top)
        hoy, sem, mes = f("circulating"), f("circulatingPrevWeek"), f("circulatingPrevMonth")
        out["stablecoins_total_mm"] = round(hoy / 1e9, 1)
        out["stablecoins_cambio_7d_mm"] = round((hoy - sem) / 1e9, 2)
        out["stablecoins_cambio_30d_mm"] = round((hoy - mes) / 1e9, 2)
    except Exception as ex:
        out["stablecoins_error"] = str(ex)[:60]
    return out


def sentimiento():
    out = {}
    try:
        f = _get("https://api.alternative.me/fng/", params={"limit": 7})["data"]
        out["fear_greed_hoy"] = {"valor": int(f[0]["value"]), "lectura": f[0]["value_classification"]}
        out["fear_greed_hace_7d"] = int(f[-1]["value"])
    except Exception as ex:
        out["fear_greed_error"] = str(ex)[:60]
    for k, inst in (("btc", "BTC-USDT-SWAP"), ("eth", "ETH-USDT-SWAP")):
        try:
            r = _get("https://www.okx.com/api/v5/public/funding-rate", params={"instId": inst})["data"][0]
            out[f"funding_{k}_pct_8h"] = round(float(r["fundingRate"]) * 100, 4)
        except Exception:
            out[f"funding_{k}_pct_8h"] = None
    return out


def regimen(mapas):
    out = {}
    for k, v in mapas.items():
        p = v.get("atr_pct")
        if p is None:
            continue
        out[k] = "volatilidad alta" if p > (5 if k == "eth" else 4) else "volatilidad baja" if p < (2.5 if k == "eth" else 2) else "volatilidad normal"
    return out


def correr(turno):
    nombre = TURNOS[turno]
    me = Mesa()
    me.paso("cio", f"Abriendo la mesa cripto · {nombre}.")

    me.paso("liquidez", "Marcando niveles de BTC y ETH: semana, mes, EMA200 diaria…")
    mapas = {}
    for k, s in ACTIVOS.items():
        try:
            mapas[k] = mapa(s)
        except Exception as ex:
            mapas[k] = {"error": str(ex)[:80]}
    me.paso("liquidez", " · ".join(f"{k.upper()} {v.get('precio', '—')} (7d {v.get('cambio_7d_pct', '—')}%)" for k, v in mapas.items()), trabajando=False)

    me.paso("macro", "Revisando dólar, tasas y Nasdaq…")
    mac = macro()
    me.paso("macro", f"DXY {mac.get('dxy', {}).get('ultimo', '—')} · 10Y {mac.get('us10y', {}).get('ultimo', '—')} · NQ 5d {mac.get('nasdaq', {}).get('cambio_5d_pct', '—')}%", trabajando=False)

    me.paso("flujos", "Midiendo stablecoins y dominancia…")
    flu = flujos()
    me.paso("flujos", f"Stablecoins 7d {flu.get('stablecoins_cambio_7d_mm', '—')} mil M · dominancia BTC {flu.get('dominancia_btc_pct', '—')}%", trabajando=False)

    me.paso("sentimiento", "Leyendo Fear & Greed y funding…")
    sen = sentimiento()
    fg = sen.get("fear_greed_hoy", {})
    me.paso("sentimiento", f"Fear & Greed {fg.get('valor', '—')} ({fg.get('lectura', '—')}) · funding BTC {sen.get('funding_btc_pct_8h', '—')}%", trabajando=False)

    me.paso("historiador", "Revisando esta misma fecha en los últimos años…")
    hist = {}
    for k, s in ACTIVOS.items():
        try:
            hist[k] = M.historico(s, anios=8)
        except Exception as ex:
            hist[k] = {"error": str(ex)[:80]}
    me.paso("historiador", " · ".join(f"{k.upper()}: {v.get('lectura', v.get('error', 'sin datos'))}" for k, v in hist.items())[:300], trabajando=False)

    me.paso("riesgo", "Midiendo volatilidad y tamaño…")
    reg = regimen(mapas)
    me.paso("riesgo", " · ".join(f"{k.upper()}: {v}" for k, v in reg.items()) or "sin datos", trabajando=False)

    me.paso("cio", "Escribiendo el brief cripto…")
    datos = {"turno": nombre, "hora_las_vegas": datetime.now(M.LV).strftime("%Y-%m-%d %H:%M"), "mapas": mapas, "macro": mac,
             "flujos": flu, "sentimiento": sen, "historico": hist, "regimen": reg}
    prompt = f"""Eres el CIO de la mesa cripto de GSAM Capital. Piensas como los grandes fondos cripto (gestores de capital
institucional, market makers y fondos macro): partes de la liquidez global (dólar, tasas, Nasdaq), miras los flujos
(stablecoins entrando o saliendo, dominancia de BTC), el apalancamiento (funding de perpetuos) y el sentimiento
(Fear & Greed, contrario en los extremos), y ves el gráfico como un mapa de liquidez. Nunca persigues precio y
proteges el capital primero: el riesgo por idea es fijo y pequeño.

Escribe el BRIEF CRIPTO · {nombre.upper()} en español, claro y directo, con estos títulos exactos:
1. **Sesgo** — una línea para BTC y una para ETH (alcista / bajista / neutral) y la razón principal.
2. **Liquidez global** — dólar, tasas y Nasdaq: ¿ayudan o frenan a cripto hoy?
3. **Flujos** — stablecoins (¿entra o sale dinero nuevo?) y dominancia (¿rotación a altcoins o refugio en BTC?).
4. **Apalancamiento y sentimiento** — funding y Fear & Greed: ¿quién está atrapado? ¿hay euforia o miedo extremo?
5. **Mapa de liquidez** — para BTC y ETH: niveles clave con número (máx/mín de la semana y del mes, EMA200 diaria) y el imán más probable.
6. **Plan** — para BTC y ETH, en este formato:
   - Dirección: COMPRA / VENTA / SIN ENTRADA
   - Zona de entrada (orden límite, nunca a mercado)
   - Stop (detrás de la liquidez que invalida la idea, con número)
   - TP1 / TP2 (en la liquidez opuesta)
   - R:R al TP2 (mínimo 2, si no: SIN ENTRADA)
   - Confluencias (0 a 5): (1) liquidez global, (2) flujos de stablecoins, (3) funding/sentimiento a favor
     (contrario en extremos), (4) zona de liquidez/valor justo, (5) estacionalidad. Menos de 4/5 = SIN ENTRADA.
7. **Historia** — qué hizo BTC en esta fecha en los últimos años y "Sesgo histórico: …". Es confluencia, nunca gatillo.
8. **Riesgo** — régimen de volatilidad y regla de tamaño: arriesgar como máximo 1% del capital por idea; en volatilidad
   alta, la mitad.
Termina con: "Análisis educativo de agentes de IA. No es consejo financiero."
Usa SOLO los números de los datos; si un dato falta dilo. Máximo 600 palabras.

DATOS:
{json.dumps(datos, ensure_ascii=False)[:12000]}"""
    texto = None
    for intento in range(1, 4):
        try:
            texto = base.gemini(prompt)
            break
        except Exception as ex:
            me.paso("cio", f"Intento {intento}/3: no pude escribir el brief ({str(ex)[:60]}).", trabajando=False)
            if intento < 3:
                time.sleep(180)
    if not texto:
        base.avisar_telefono("GSAM Cripto · sin brief", "La mesa cripto no pudo escribir el brief.", "high")
        return

    try:
        corto = base.gemini_json(f"""Del siguiente brief, extrae en JSON: {{"titular": "una frase", "sesgo": {{"btc": "alcista|bajista|neutral",
"eth": "alcista|bajista|neutral"}}, "entradas": [{{"activo": "BTC|ETH", "direccion": "COMPRA|VENTA|SIN ENTRADA", "zona": "", "stop": "",
"tp1": "", "tp2": "", "rr": "", "confluencias": ""}}]}}\n\nBRIEF:\n{texto[:6000]}""", lite=True)
    except Exception:
        corto = {"titular": texto.split("\n")[0][:140], "sesgo": {}, "entradas": []}

    me.m["briefs"].insert(0, {"turno": nombre, "ts": base.ahora(), "texto": texto, **corto,
                              "precios": {k: v.get("precio") for k, v in mapas.items()},
                              "fear_greed": fg.get("valor"), "dominancia_btc": flu.get("dominancia_btc_pct")})
    me.paso("cio", f"Brief publicado: {corto.get('titular', '')}", trabajando=False)
    lineas = [f"{e.get('activo')}: {e.get('direccion')} {e.get('zona', '')} · SL {e.get('stop', '')} · TP2 {e.get('tp2', '')} · {e.get('confluencias', '')}"
              for e in corto.get("entradas", []) if not str(e.get("direccion", "")).upper().startswith("SIN")]
    try:
        ejecutar_cripto(me, corto.get("entradas", []), mapas)
    except Exception as ex:
        me.paso("ejecucion", f"Error en la ejecución ({str(ex)[:80]}).", trabajando=False)
    s = corto.get("sesgo", {})
    base.avisar_telefono(f"GSAM Cripto · BTC {s.get('btc', '?')} · ETH {s.get('eth', '?')}",
                         (corto.get("titular", "") + ("\n" + "\n".join(lineas) if lineas else "\nSin entrada de alta probabilidad."))[:900], "default")


# ───────── ejecución en Blockchain.com Exchange (solo spot: solo compras) ─────────
# Seguridad: la llave de API se crea SOLO con permiso de operar (nunca de retirar) y vive en el secreto
# BLOCKCHAIN_API_KEY de GitHub. Mientras el secreto MODO_CRIPTO no diga "real", todo es simulado.

API_BC = "https://api.blockchain.com/v3/exchange"
RIESGO_PCT = 1.0           # % del capital que se arriesga por idea
MAX_POSICION_PCT = 40.0    # nunca más de este % del capital en una sola posición
VENCE_HORAS = 24           # si la compra límite no se llena en 24 h, se cancela
DECIMALES = {"BTC": 5, "ETH": 4}


def _modo_real():
    return os.environ.get("MODO_CRIPTO", "").strip().lower() == "real" and bool(os.environ.get("BLOCKCHAIN_API_KEY", "").strip())


def _bc(metodo, ruta, **kw):
    key = os.environ.get("BLOCKCHAIN_API_KEY", "").strip()
    r = requests.request(metodo, API_BC + ruta, headers={"X-API-Token": key, "Accept": "application/json"}, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Blockchain.com {r.status_code}: {r.text[:120]}")
    return r.json() if r.text.strip() else {}


def _saldos():
    """Saldos disponibles de la cuenta principal: {"USD": 123.4, "BTC": 0.001, ...}."""
    d = _bc("GET", "/accounts")
    filas = d.get("primary") if isinstance(d, dict) else d
    out = {}
    for f in filas or []:
        try:
            out[f["currency"]] = float(f.get("available", f.get("balance", 0)))
        except Exception:
            pass
    return out


def _orden(simbolo, lado, qty, tipo="LIMIT", precio=None, stop=None, ref=""):
    cuerpo = {"clOrdId": f"gsam{ref}{int(time.time())}"[:20], "ordType": tipo, "symbol": simbolo, "side": lado,
              "orderQty": qty, "timeInForce": "GTC"}
    if precio is not None:
        cuerpo["price"] = precio
    if stop is not None:
        cuerpo["stopPx"] = stop
    return _bc("POST", "/orders", json=cuerpo)


def ejecutar_cripto(me, entradas, mapas):
    real = _modo_real()
    etiqueta = "" if real else "[SIMULADO] "
    pos = me.m.setdefault("posiciones", [])
    capital = None
    if real:
        try:
            sal = _saldos()
            capital = sal.get("USD", 0) + sum(sal.get(a, 0) * (mapas.get(a.lower(), {}).get("precio") or 0) for a in ("BTC", "ETH"))
            usd_libre = sal.get("USD", 0)
        except Exception as ex:
            me.paso("ejecucion", f"No pude leer tu cuenta de Blockchain.com ({str(ex)[:80]}). No se opera.", trabajando=False)
            return
    else:
        capital, usd_libre = 1000.0, 1000.0   # cuenta de práctica para la simulación
    for e in entradas or []:
        act = str(e.get("activo", "")).upper()
        if act not in ("BTC", "ETH"):
            continue
        if not str(e.get("direccion", "")).upper().startswith("COMPRA"):
            me.paso("ejecucion", f"{act}: {e.get('direccion', 'SIN ENTRADA')} · en spot solo se compra, no se opera.", trabajando=False)
            continue
        if any(p["activo"] == act and p["estado"] in ("pendiente", "abierta") for p in pos):
            me.paso("ejecucion", f"{act}: ya hay una posición u orden activa, no se duplica.", trabajando=False)
            continue
        try:
            conf = int(M._n(e.get("confluencias")) or 0)
        except Exception:
            conf = 0
        lo, hi = M._rango(e.get("zona"))
        stop, tp = M._n(e.get("stop")), M._n(e.get("tp2")) or M._n(e.get("tp1"))
        if conf < 4 or not (lo and stop and tp) or not (stop < lo < tp):
            me.paso("ejecucion", f"{act}: entrada incompleta o con {conf}/5 confluencias. No se envía.", trabajando=False)
            continue
        entrada = lo   # borde de la zona (donde está la liquidez)
        rr = (tp - entrada) / (entrada - stop)
        if rr < 2:
            me.paso("ejecucion", f"{act}: R:R {rr:.1f} menor a 2. No se envía.", trabajando=False)
            continue
        riesgo = capital * RIESGO_PCT / 100
        qty = riesgo / (entrada - stop)
        qty = min(qty, capital * MAX_POSICION_PCT / 100 / entrada, usd_libre * 0.98 / entrada)
        qty = round(qty, DECIMALES[act])
        if qty <= 0:
            me.paso("ejecucion", f"{act}: no hay saldo en USD suficiente para la compra.", trabajando=False)
            continue
        simbolo = f"{act}-USD"
        p = {"activo": act, "simbolo": simbolo, "qty": qty, "entrada": round(entrada, 2), "stop": round(stop, 2), "tp": round(tp, 2),
             "estado": "pendiente", "creada": base.ahora(), "real": real, "orden": None, "orden_stop": None}
        if real:
            try:
                r = _orden(simbolo, "BUY", qty, "LIMIT", precio=p["entrada"], ref=act.lower())
                p["orden"] = r.get("exOrdId") or r.get("orderId") or r.get("clOrdId")
            except Exception as ex:
                me.paso("ejecucion", f"{act}: no pude enviar la orden ({str(ex)[:80]}).", trabajando=False)
                continue
        pos.insert(0, p)
        txt = (f"{etiqueta}COMPRA límite {qty} {act} en {p['entrada']:,} · stop {p['stop']:,} · objetivo {p['tp']:,} · "
               f"riesgo ${(entrada - stop) * qty:,.0f} ({RIESGO_PCT}% del capital) · R:R {rr:.1f}")
        me.paso("ejecucion", txt, trabajando=False)
        base.avisar_telefono(f"GSAM Cripto · orden {act}", txt, "high")
    me.m["posiciones"] = pos[:50]


def vigilar_cripto():
    """Revisa las órdenes: pone el stop cuando la compra se llena, vende en el objetivo y cancela compras viejas."""
    me = Mesa()
    pos = me.m.get("posiciones", [])
    activas = [p for p in pos if p["estado"] in ("pendiente", "abierta")]
    if not activas:
        return
    real = _modo_real()
    for p in activas:
        try:
            precio = M.yahoo(f"{p['activo']}-USD", "1h", "1d")[-1]["c"]
        except Exception:
            continue
        horas = (datetime.now(timezone.utc) - datetime.fromisoformat(p["creada"].replace("Z", "+00:00"))).total_seconds() / 3600
        if p["estado"] == "pendiente":
            lleno = False
            if real and p.get("orden"):
                try:
                    o = _bc("GET", f"/orders/{p['orden']}")
                    lleno = str(o.get("ordStatus", "")).upper() == "FILLED"
                except Exception:
                    pass
            elif not real:
                lleno = precio <= p["entrada"]
            if lleno:
                p["estado"] = "abierta"
                if real:
                    try:
                        r = _orden(p["simbolo"], "SELL", p["qty"], "STOP", stop=p["stop"], ref="sl")
                        p["orden_stop"] = r.get("exOrdId") or r.get("orderId")
                    except Exception as ex:
                        me.paso("ejecucion", f"{p['activo']}: ¡no pude poner el stop! ({str(ex)[:60]}) Revísalo a mano.", trabajando=False)
                        base.avisar_telefono("GSAM Cripto · ¡STOP SIN PONER!", f"{p['activo']}: la compra se llenó pero no pude poner el stop en {p['stop']}. Ponlo a mano.", "urgent")
                me.paso("ejecucion", f"{'' if real else '[SIMULADO] '}{p['activo']}: compra llena en {p['entrada']:,}. Stop protegiendo en {p['stop']:,}.", trabajando=False)
                base.avisar_telefono(f"GSAM Cripto · {p['activo']} dentro", f"Compra llena en {p['entrada']:,}. Stop en {p['stop']:,}, objetivo {p['tp']:,}.", "high")
            elif horas > VENCE_HORAS:
                if real and p.get("orden"):
                    try:
                        _bc("DELETE", f"/orders/{p['orden']}")
                    except Exception:
                        pass
                p["estado"] = "cancelada"
                me.paso("ejecucion", f"{p['activo']}: la compra no se llenó en {VENCE_HORAS} h. Cancelada.", trabajando=False)
        elif p["estado"] == "abierta":
            if precio <= p["stop"] and not real:
                p["estado"], p["resultado"] = "cerrada", "stop"
            elif precio >= p["tp"]:
                if real:
                    try:
                        if p.get("orden_stop"):
                            _bc("DELETE", f"/orders/{p['orden_stop']}")
                        _orden(p["simbolo"], "SELL", p["qty"], "LIMIT", precio=round(precio * 0.999, 2), ref="tp")
                    except Exception as ex:
                        me.paso("ejecucion", f"{p['activo']}: no pude vender en el objetivo ({str(ex)[:60]}).", trabajando=False)
                        continue
                p["estado"], p["resultado"] = "cerrada", "objetivo"
            elif real and p.get("orden_stop"):
                try:
                    o = _bc("GET", f"/orders/{p['orden_stop']}")
                    if str(o.get("ordStatus", "")).upper() == "FILLED":
                        p["estado"], p["resultado"] = "cerrada", "stop"
                except Exception:
                    pass
            if p["estado"] == "cerrada":
                ganancia = (p["tp"] if p["resultado"] == "objetivo" else p["stop"]) - p["entrada"]
                txt = f"{'' if real else '[SIMULADO] '}{p['activo']} cerrada en {p['resultado']}: {ganancia * p['qty']:+,.0f} USD."
                me.paso("ejecucion", txt, trabajando=False)
                base.avisar_telefono(f"GSAM Cripto · {p['activo']} cerrada", txt, "high")
    me.m["posiciones"] = pos
    me.guardar("vigilancia de posiciones")


if __name__ == "__main__":
    t = (sys.argv[1] if len(sys.argv) > 1 else "manana").lower()
    if t == "vigilar":
        vigilar_cripto()
        sys.exit(0)
    if t not in TURNOS:
        sys.exit("Uso: python agentes/cripto.py <manana|noche|vigilar>")
    correr(t)
