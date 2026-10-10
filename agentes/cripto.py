"""Mesa cripto de GSAM Capital · BTC y ETH.

Siete agentes con mentalidad de fondo cripto institucional preparan el brief dos veces al día:
  macro        → dólar, tasas y Nasdaq (el apetito de riesgo que mueve a cripto)
  flujos       → stablecoins (dinero nuevo entrando o saliendo) y dominancia de BTC
  sentimiento  → Fear & Greed y funding de perpetuos (¿quién está apalancado?)
  liquidez     → niveles: máximos/mínimos de la semana y del mes, EMA200 diaria, valor justo
  historiador  → estacionalidad de BTC en los últimos años y años análogos
  riesgo       → volatilidad (ATR), régimen y tamaño de posición
  cio          → junta todo y escribe el brief con sesgo, zonas y escenarios
  ejecucion    → (opcional) compra BTC on-chain en Solana (Jupiter) desde la billetera del bot, riesgo 1% y stop vigilado

Uso: python agentes/cripto.py <manana|noche|vigilar|billetera|preparar|devolver>
Todo queda en data/cripto.json (la página cripto.html lo lee en vivo).
Es análisis educativo; no es consejo financiero. Solo ejecuta si MODO_CRIPTO=real.
"""
import json
import os
import re
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
     Escribe las confluencias SIEMPRE como "N/5".
   - Sesgo neutral NO impide operar: si hay un soporte (o resistencia) clave con 4/5 o más y R:R mínimo 2, propone
     COMPRA (o VENTA) con orden límite esperando en esa zona, como los fondos que dejan órdenes en los niveles.
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
"tp1": "", "tp2": "", "rr": "", "confluencias": "N/5 (solo cuántas confluencias se cumplen, ej. 4/5)"}}]}}\n\nBRIEF:\n{texto[:6000]}""", lite=True)
    except Exception:
        corto = {"titular": texto.split("\n")[0][:140], "sesgo": {}, "entradas": []}

    me.m["briefs"].insert(0, {"turno": nombre, "ts": base.ahora(), "texto": texto, **corto,
                              "precios": {k: v.get("precio") for k, v in mapas.items()},
                              "fear_greed": fg.get("valor"), "dominancia_btc": flu.get("dominancia_btc_pct")})
    me.paso("cio", f"Brief publicado: {corto.get('titular', '')}", trabajando=False)
    lineas = [f"{e.get('activo')}: {e.get('direccion')} {e.get('zona', '')} · SL {e.get('stop', '')} · TP2 {e.get('tp2', '')} · {e.get('confluencias', '')}"
              for e in corto.get("entradas", []) if not str(e.get("direccion", "")).upper().startswith("SIN")]
    entradas = []
    for e in corto.get("entradas", []) or []:
        e2 = dict(e)
        d = _direccion(e, (corto.get("sesgo") or {}).get(str(e.get("activo", "")).lower()))
        if d != "SIN ENTRADA" and not str(e.get("direccion", "")).upper().startswith(d):
            e2["direccion"] = d
            me.paso("cio", f"{e.get('activo')}: zona clave con {_conf(e.get('confluencias'))}/5 → orden límite de {d} esperando en {e.get('zona')}.", trabajando=False)
        entradas.append(e2)
    try:
        ejecutar_cripto(me, entradas, mapas)
    except Exception as ex:
        me.paso("ejecucion", f"Error en la ejecución ({str(ex)[:80]}).", trabajando=False)
    try:
        ejecutar_desplazamiento(me, corto.get("sesgo") or {})
    except Exception as ex:
        me.paso("futuros", f"Error en desplazamiento ({str(ex)[:80]}).", trabajando=False)
    try:
        enviar_futuros(me, entradas)
    except Exception as ex:
        me.paso("futuros", f"Error enviando futuros ({str(ex)[:80]}).", trabajando=False)
    s = corto.get("sesgo", {})
    base.avisar_telefono(f"GSAM Cripto · BTC {s.get('btc', '?')} · ETH {s.get('eth', '?')}",
                         (corto.get("titular", "") + ("\n" + "\n".join(lineas) if lineas else "\nSin entrada de alta probabilidad."))[:900], "default")


# ───────── ejecución on-chain en Solana con Jupiter (sin exchange, sin KYC) ─────────
# La billetera del bot es una cuenta NUEVA de Phantom solo con el capital del bot. Su llave privada vive en el
# secreto SOLANA_BOT_KEY de GitHub. Mientras el secreto MODO_CRIPTO no diga "real", todo es simulado.
# En un DEX no hay órdenes límite ni stops en el libro: el vigilante revisa el precio cada 30 min y compra en la
# zona, vende en el stop o vende en el objetivo con un swap de Jupiter. Solo BTC (cbBTC de Coinbase) y solo compras.

RPC = os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
JUP = "https://lite-api.jup.ag/swap/v1"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKENS = {"BTC": {"mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij", "dec": 8}}
RIESGO_PCT = 1.0           # % del capital que se arriesga por idea
MAX_POSICION_PCT = 90.0    # cuenta pequeña: puede usar casi todo el USDC en una idea (el riesgo sigue siendo 1%)
VENCE_HORAS = 24           # si el precio no llega a la zona en 24 h, la idea se cancela
PAUSA_HORAS = 72           # freno: tras 2 stops seguidos, no se abren ideas nuevas en 72 h
SLIPPAGE_BPS = 50


def _modo_real():
    return os.environ.get("MODO_CRIPTO", "").strip().lower() == "real" and bool(os.environ.get("SOLANA_BOT_KEY", "").strip())


def _llave():
    from solders.keypair import Keypair
    return Keypair.from_base58_string(os.environ["SOLANA_BOT_KEY"].strip())


def _rpc(metodo, params):
    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"RPC {j['error'].get('message', j['error'])}")
    return j["result"]


def _saldo_token(dueno, mint):
    res = _rpc("getTokenAccountsByOwner", [dueno, {"mint": mint}, {"encoding": "jsonParsed"}])
    return sum(float(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0) for a in res.get("value", []))


def _saldos():
    dueno = str(_llave().pubkey())
    sol = _rpc("getBalance", [dueno])["value"] / 1e9
    return {"SOL": sol, "USDC": _saldo_token(dueno, USDC), "BTC": _saldo_token(dueno, TOKENS["BTC"]["mint"]), "direccion": dueno}


def _swap(entrada_mint, salida_mint, cantidad_base):
    """Swap en Jupiter firmado con la billetera del bot. Devuelve la firma de la transacción."""
    import base64
    from solders.transaction import VersionedTransaction
    kp = _llave()
    q = requests.get(f"{JUP}/quote", params={"inputMint": entrada_mint, "outputMint": salida_mint, "amount": int(cantidad_base),
                                             "slippageBps": SLIPPAGE_BPS, "restrictIntermediateTokens": "true"}, timeout=30)
    q.raise_for_status()
    cot = q.json()
    if not cot.get("outAmount"):
        raise RuntimeError("Jupiter no dio cotización")
    sw = requests.post(f"{JUP}/swap", json={"quoteResponse": cot, "userPublicKey": str(kp.pubkey()), "wrapAndUnwrapSol": True,
                                            "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": "auto"}, timeout=30)
    sw.raise_for_status()
    tx = VersionedTransaction.from_bytes(base64.b64decode(sw.json()["swapTransaction"]))
    firmada = VersionedTransaction(tx.message, [kp])
    sig = _rpc("sendTransaction", [base64.b64encode(bytes(firmada)).decode(), {"encoding": "base64", "maxRetries": 3}])
    for _ in range(30):
        time.sleep(2)
        st = _rpc("getSignatureStatuses", [[sig]])["value"][0]
        if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
            if st.get("err"):
                raise RuntimeError(f"la transacción falló en la red: {st['err']}")
            return sig
    raise RuntimeError(f"sin confirmación todavía (firma {sig[:12]}…)")


def _comprar(p, usd):
    return _swap(USDC, TOKENS[p["activo"]]["mint"], usd * 1e6)


def _vender_todo(p):
    sal = _saldos()
    cant = sal.get(p["activo"], 0)
    if cant <= 0:
        raise RuntimeError("no hay saldo para vender")
    return _swap(TOKENS[p["activo"]]["mint"], USDC, cant * 10 ** TOKENS[p["activo"]]["dec"])


def ejecutar_cripto(me, entradas, mapas):
    """Tras el brief: registra la idea de compra. El vigilante la ejecuta cuando el precio llega a la zona."""
    real = _modo_real()
    etiqueta = "" if real else "[SIMULADO] "
    pos = me.m.setdefault("posiciones", [])
    cerradas = [p for p in pos if p.get("estado") == "cerrada"]
    if len(cerradas) >= 2 and all(c.get("resultado") == "stop" for c in cerradas[:2]):
        ult = datetime.fromisoformat((cerradas[0].get("cerrada_ts") or cerradas[0]["creada"]).replace("Z", "+00:00"))
        horas = (datetime.now(timezone.utc) - ult).total_seconds() / 3600
        if horas < PAUSA_HORAS:
            me.paso("riesgo", f"Freno de pérdidas: 2 stops seguidos. No se abren ideas nuevas por {PAUSA_HORAS - horas:.0f} h más.", trabajando=False)
            if me.m.get("freno_avisado") != cerradas[0].get("cerrada_ts"):
                base.avisar_telefono("GSAM Cripto · freno de pérdidas", f"2 pérdidas seguidas: el bot se pausa {PAUSA_HORAS} h para proteger el capital.", "high")
                me.m["freno_avisado"] = cerradas[0].get("cerrada_ts")
            return
    if real:
        try:
            sal = _saldos()
            capital = sal["USDC"] + sal["BTC"] * (mapas.get("btc", {}).get("precio") or 0)
            usd_libre = sal["USDC"]
            if sal["SOL"] < 0.005:
                me.paso("ejecucion", f"La billetera del bot tiene muy poco SOL ({sal['SOL']:.4f}) para comisiones. Agrega ~0.02 SOL.", trabajando=False)
        except Exception as ex:
            me.paso("ejecucion", f"No pude leer la billetera del bot ({str(ex)[:80]}). No se opera.", trabajando=False)
            return
    else:
        capital, usd_libre = 60.0, 60.0   # cuenta de práctica para la simulación
    for e in entradas or []:
        act = str(e.get("activo", "")).upper()
        if act not in TOKENS:
            continue
        if not str(e.get("direccion", "")).upper().startswith("COMPRA"):
            me.paso("ejecucion", f"{act}: {e.get('direccion', 'SIN ENTRADA')} · el bot solo compra, no se opera.", trabajando=False)
            continue
        if any(p["activo"] == act and p["estado"] in ("pendiente", "abierta") for p in pos):
            me.paso("ejecucion", f"{act}: ya hay una idea o posición activa, no se duplica.", trabajando=False)
            continue
        conf = _conf(e.get("confluencias"))
        lo, hi = M._rango(e.get("zona"))
        stop, tp = M._n(e.get("stop")), M._n(e.get("tp2")) or M._n(e.get("tp1"))
        if conf < 4 or not (lo and hi and stop and tp) or not (stop < lo <= hi < tp):
            me.paso("ejecucion", f"{act}: entrada incompleta o con {conf}/5 confluencias. No se opera.", trabajando=False)
            continue
        rr = (tp - hi) / (hi - stop)
        if rr < 2:
            me.paso("ejecucion", f"{act}: R:R {rr:.1f} menor a 2. No se opera.", trabajando=False)
            continue
        usd = min(capital * RIESGO_PCT / 100 / ((hi - stop) / hi), capital * MAX_POSICION_PCT / 100, usd_libre * 0.98)
        if usd < 5:
            me.paso("ejecucion", f"{act}: el tamaño quedaría en ${usd:.2f}, demasiado pequeño. No se opera.", trabajando=False)
            continue
        tp1 = M._n(e.get("tp1"))
        p = {"activo": act, "usd": round(usd, 2), "zona_baja": round(lo, 2), "entrada": round(hi, 2), "stop": round(stop, 2), "tp": round(tp, 2),
             "tp1": round(tp1, 2) if tp1 and hi < tp1 < tp else None,
             "estado": "pendiente", "creada": base.ahora(), "real": real}
        pos.insert(0, p)
        txt = (f"{etiqueta}Idea de COMPRA {act}: compra ${p['usd']} si el precio entra en {lo:,.0f}–{hi:,.0f} · stop {stop:,.0f} · "
               f"objetivo {tp:,.0f} · riesgo ~${p['usd'] * (hi - stop) / hi:,.2f} ({RIESGO_PCT}% del capital) · R:R {rr:.1f}")
        me.paso("ejecucion", txt, trabajando=False)
        base.avisar_telefono(f"GSAM Cripto · idea {act}", txt, "high")
    me.m["posiciones"] = pos[:50]


# ───────── futuros micro de CME en la cuenta DEMO (TradersPost → Tradovate): compras Y ventas ─────────
# BTC → MBT (Micro Bitcoin, 0.1 BTC) · ETH → MET (Micro Ether, 0.1 ETH). Riesgo fijo $350, tamaño variable, máx 10.
RIESGO_FUT = 350
FUT = {"BTC": {"ticker": "MBT", "vp": 0.1, "tick": 5.0, "margen": 50, "stop_min": 150, "max": 10},
       "ETH": {"ticker": "MET", "vp": 0.1, "tick": 0.5, "margen": 3, "stop_min": 10, "max": 10}}


def _cme_abierto():
    """CME cripto: domingo 6 pm ET a viernes 5 pm ET (aprox. en UTC)."""
    t = datetime.now(timezone.utc)
    d, h = t.weekday(), t.hour
    return not (d == 5 or (d == 4 and h >= 21) or (d == 6 and h < 22))


def _conf(x):
    """Confluencias como número: acepta "4/5", "4 de 5" o una lista "(1) … (2) …"."""
    s = str(x or "")
    m = re.search(r"(\d)\s*(?:/|de)\s*5", s)
    if m:
        return int(m.group(1))
    marcas = set(re.findall(r"\((\d)\)", s))
    if marcas:
        return len(marcas)
    m = re.search(r"\d", s)
    return int(m.group()) if m else 0


def _direccion(e, sesgo):
    """COMPRA/VENTA/SIN ENTRADA. Si el CIO dejó una zona clave completa (4/5+) sin dirección y el sesgo no va en contra,
    se trata como orden límite esperando en la zona, como hacen los fondos."""
    d = str(e.get("direccion", "")).upper()
    if d.startswith("COMPRA"):
        return "COMPRA"
    if d.startswith("VENTA"):
        return "VENTA"
    lo, hi = M._rango(e.get("zona"))
    stop, tp = M._n(e.get("stop")), M._n(e.get("tp2")) or M._n(e.get("tp1"))
    s = str(sesgo or "").lower()
    if _conf(e.get("confluencias")) >= 4 and lo and hi and stop and tp:
        if stop < lo <= hi < tp and (tp - hi) / (hi - stop) >= 2 and "bajista" not in s:
            return "COMPRA"
        if tp < lo <= hi < stop and (lo - tp) / (stop - lo) >= 2 and "alcista" not in s:
            return "VENTA"
    return "SIN ENTRADA"


def enviar_futuros(me, entradas):
    url = os.environ.get("TRADERSPOST_WEBHOOK", "").strip()
    if not url:
        return
    if not _cme_abierto():
        me.paso("futuros", "Mercado de futuros CME cerrado (fin de semana). No se envían órdenes.", trabajando=False)
        return
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    env = me.m.setdefault("futuros_enviados", {})
    for e in entradas or []:
        act = str(e.get("activo", "")).upper()
        c = FUT.get(act)
        d = str(e.get("direccion", "")).upper()
        if not c or d.startswith("SIN"):
            continue
        compra = d.startswith("COMPRA")
        if env.get(act) == hoy:
            me.paso("futuros", f"{c['ticker']}: ya se envió una orden hoy.", trabajando=False)
            continue
        conf = _conf(e.get("confluencias"))
        lo, hi = M._rango(e.get("zona"))
        stop, tp = M._n(e.get("stop")), M._n(e.get("tp2")) or M._n(e.get("tp1"))
        if conf < 4 or not (lo and hi and stop and tp):
            me.paso("futuros", f"{c['ticker']}: entrada incompleta o con {conf}/5 confluencias. No se envía.", trabajando=False)
            continue
        r = lambda x: round(round(x / c["tick"]) * c["tick"], 2)
        if compra:
            ent = lo
            stop = min(stop, lo - c["margen"], ent - c["stop_min"])
            ok = stop < ent < tp
        else:
            ent = hi
            stop = max(stop, hi + c["margen"], ent + c["stop_min"])
            ok = tp < ent < stop
        ent, stop, tp = r(ent), r(stop), r(tp)
        if not ok:
            me.paso("futuros", f"{c['ticker']}: niveles incoherentes. No se envía.", trabajando=False)
            continue
        pts = abs(ent - stop)
        rr = abs(tp - ent) / pts
        if rr < 2:
            me.paso("futuros", f"{c['ticker']}: R:R {rr:.1f} menor a 2. No se envía.", trabajando=False)
            continue
        q = min(c["max"], int(RIESGO_FUT // (pts * c["vp"])))
        if q < 1:
            me.paso("futuros", f"{c['ticker']}: stop muy amplio para ${RIESGO_FUT}. No se envía.", trabajando=False)
            continue
        orden = {"ticker": c["ticker"], "action": "buy" if compra else "sell", "orderType": "limit", "limitPrice": ent,
                 "price": ent, "quantity": q, "stopLoss": {"type": "stop", "stopPrice": stop}, "takeProfit": {"limitPrice": tp}}
        try:
            resp = requests.post(url, json=orden, timeout=20)
            ok_envio = resp.status_code < 300
        except Exception as ex:
            ok_envio, resp = False, ex
        lado = "COMPRA" if compra else "VENTA"
        txt = (f"[DEMO] {lado} {q} {c['ticker']} límite {ent:,.2f} · stop {stop:,.2f} · objetivo {tp:,.2f} · "
               f"riesgo ${pts * c['vp'] * q:,.0f} · R:R {rr:.1f}")
        if ok_envio:
            env[act] = hoy
            me.paso("futuros", txt, trabajando=False)
            base.avisar_telefono(f"GSAM Cripto · {lado} {c['ticker']} (demo)", txt, "high")
        else:
            me.paso("futuros", f"{c['ticker']}: TradersPost no aceptó la orden ({str(getattr(resp, 'status_code', resp))[:60]}).", trabajando=False)


# ───────── estrategia de Gent: vela de desplazamiento (1H) → orden límite en el 50% del impulso ─────────
# Igual que su indicador de TradingView: vela grande (cuerpo ≥ 3 ATR y ≥ 70% de la vela), se sigue el impulso hasta que
# retrocede 20%, orden límite en el 50%, stop en el 62% + margen, objetivo en el extremo del impulso. Compras y VENTAS.
DESP = {"BTC": {"sim": "BTC-USD", "margen": 60}, "ETH": {"sim": "ETH-USD", "margen": 4}}


def desplazamiento(act):
    c = DESP[act]
    v = M.yahoo(c["sim"], "1h", "7d")
    if len(v) < 30:
        return None
    fase, dirx, ini, ext, orden, t0 = 0, 0, None, None, None, None
    for i in range(15, len(v)):
        x, a = v[i], M.atr(v[:i], 14) or 1
        cuerpo, rango = abs(x["c"] - x["o"]), (x["h"] - x["l"]) or 1
        if fase in (0, 3) and cuerpo >= 3 * a and cuerpo >= 0.7 * rango:
            dirx = -1 if x["c"] < x["o"] else 1
            ini, ext, t0, fase, orden = (x["h"] if dirx < 0 else x["l"]), (x["l"] if dirx < 0 else x["h"]), x["t"], 1, None
            continue
        if fase == 1:
            ext = min(ext, x["l"]) if dirx < 0 else max(ext, x["h"])
            leg = abs(ini - ext)
            if (x["h"] > ini) if dirx < 0 else (x["l"] < ini):
                fase = 0
                continue
            retro = (x["c"] - ext) if dirx < 0 else (ext - x["c"])
            if leg > 0 and retro >= 0.2 * leg:
                ent = ext + 0.5 * leg if dirx < 0 else ext - 0.5 * leg
                sl = ext + 0.62 * leg + c["margen"] if dirx < 0 else ext - 0.62 * leg - c["margen"]
                orden, fase = {"dir": "VENTA" if dirx < 0 else "COMPRA", "entrada": round(ent, 2), "stop": round(sl, 2),
                               "objetivo": round(ext, 2), "id": f"{act}-{t0}-{round(ext)}"}, 2
            continue
        if fase == 2:
            if (x["l"] < ext) if dirx < 0 else (x["h"] > ext):
                ext, fase = (x["l"] if dirx < 0 else x["h"]), 1          # el impulso siguió: se recalcula
            elif (x["h"] >= orden["entrada"]) if dirx < 0 else (x["l"] <= orden["entrada"]):
                fase = 3                                                  # ya se llenó antes: no se persigue
            elif x["t"] - t0 > 72 * 3600:
                fase = 0
    if fase == 2 and orden:
        orden["rr"] = round(abs(orden["objetivo"] - orden["entrada"]) / abs(orden["stop"] - orden["entrada"]), 2)
        orden["precio"] = round(v[-1]["c"], 2)
        return orden
    return None


def ejecutar_desplazamiento(me, sesgos=None):
    """Busca la vela de desplazamiento en BTC y ETH y manda la orden a la cuenta DEMO (MBT/MET) en compra o en venta.
    No va contra el sesgo de la mesa. Una orden por setup."""
    url = os.environ.get("TRADERSPOST_WEBHOOK", "").strip()
    sesgos = sesgos or ((me.m.get("briefs") or [{}])[0].get("sesgo") or {})
    hechos = me.m.setdefault("desp_enviados", [])
    for act in DESP:
        try:
            o = desplazamiento(act)
        except Exception as ex:
            print("desplazamiento", act, ex)
            continue
        if not o or o["id"] in hechos:
            continue
        s = str(sesgos.get(act.lower(), "")).lower()
        if (o["dir"] == "VENTA" and "alcista" in s) or (o["dir"] == "COMPRA" and "bajista" in s):
            me.paso("futuros", f"{act}: vela de desplazamiento {o['dir']} pero el sesgo es {s}. No se opera.", trabajando=False)
            hechos.append(o["id"])
            continue
        if o["rr"] < 1.5:
            hechos.append(o["id"])
            continue
        c = FUT[act]
        if not url or not _cme_abierto():
            av = me.m.setdefault("desp_avisados", [])
            if o["id"] in av:
                continue
            av.append(o["id"])
            me.m["desp_avisados"] = av[-40:]
            base.avisar_telefono(f"GSAM Cripto · {act} desplazamiento {o['dir']}", f"Entrada {o['entrada']:,.0f} · stop {o['stop']:,.0f} · objetivo {o['objetivo']:,.0f}. CME cerrado: la orden demo sale cuando abra.", "default")
            me.paso("futuros", f"{act}: setup de desplazamiento {o['dir']} en {o['entrada']:,.0f} (stop {o['stop']:,.0f}, objetivo {o['objetivo']:,.0f}). "
                               "Mercado CME cerrado: se envía cuando abra.", trabajando=False)
            continue
        pts = abs(o["entrada"] - o["stop"])
        q = min(c["max"], int(RIESGO_FUT // (pts * c["vp"])))
        if q < 1:
            me.paso("futuros", f"{act}: desplazamiento con stop muy amplio para ${RIESGO_FUT}. No se envía.", trabajando=False)
            hechos.append(o["id"])
            continue
        r = lambda x: round(round(x / c["tick"]) * c["tick"], 2)
        compra = o["dir"] == "COMPRA"
        orden = {"ticker": c["ticker"], "action": "buy" if compra else "sell", "orderType": "limit", "limitPrice": r(o["entrada"]),
                 "price": r(o["entrada"]), "quantity": q, "stopLoss": {"type": "stop", "stopPrice": r(o["stop"])},
                 "takeProfit": {"limitPrice": r(o["objetivo"])}}
        try:
            ok = requests.post(url, json=orden, timeout=20).status_code < 300
        except Exception:
            ok = False
        if ok:
            hechos.append(o["id"])
            txt = (f"[DEMO] Vela de desplazamiento · {o['dir']} {q} {c['ticker']} límite {orden['limitPrice']:,} (50% del impulso) · "
                   f"stop {orden['stopLoss']['stopPrice']:,} · objetivo {orden['takeProfit']['limitPrice']:,} · R:R {o['rr']}")
            me.paso("futuros", txt, trabajando=False)
            base.avisar_telefono(f"GSAM Cripto · {o['dir']} {c['ticker']} (desplazamiento)", txt, "high")
    me.m["desp_enviados"] = hechos[-40:]


def vigilar_cripto():
    """Cada 30 min: compra cuando el precio entra en la zona, vende en el stop o en el objetivo, y cancela ideas viejas."""
    me = Mesa()
    try:
        ejecutar_desplazamiento(me)
    except Exception as ex:
        print("desplazamiento", ex)
    pos = me.m.get("posiciones", [])
    activas = [p for p in pos if p["estado"] in ("pendiente", "abierta")]
    if not activas:
        me.guardar("vigilancia de desplazamiento")
        return
    real = _modo_real()
    et = "" if real else "[SIMULADO] "
    for p in activas:
        try:
            precio = M.yahoo(f"{p['activo']}-USD", "5m", "1d")[-1]["c"]
        except Exception:
            continue
        horas = (datetime.now(timezone.utc) - datetime.fromisoformat(p["creada"].replace("Z", "+00:00"))).total_seconds() / 3600
        try:
            if p["estado"] == "pendiente":
                if precio <= p["stop"]:
                    p["estado"] = "cancelada"
                    me.paso("ejecucion", f"{p['activo']}: el precio rompió el stop antes de entrar. Idea cancelada.", trabajando=False)
                elif precio <= p["entrada"]:
                    if real:
                        p["firma_compra"] = _comprar(p, p["usd"])
                    p["estado"], p["precio_compra"] = "abierta", round(precio, 2)
                    txt = f"{et}{p['activo']}: COMPRADO ${p['usd']} a ~{precio:,.0f}. Stop {p['stop']:,.0f} · objetivo {p['tp']:,.0f}."
                    me.paso("ejecucion", txt, trabajando=False)
                    base.avisar_telefono(f"GSAM Cripto · {p['activo']} dentro", txt, "high")
                elif horas > VENCE_HORAS:
                    p["estado"] = "cancelada"
                    me.paso("ejecucion", f"{p['activo']}: el precio no llegó a la zona en {VENCE_HORAS} h. Idea cancelada.", trabajando=False)
            elif p["estado"] == "abierta":
                if p.get("tp1") and not p.get("be") and precio >= p["tp1"]:
                    p["stop"], p["be"] = p.get("precio_compra") or p["entrada"], True
                    txt = f"{et}{p['activo']}: llegó al TP1 ({p['tp1']:,.0f}). Stop movido a la entrada ({p['stop']:,.0f}): ya no puede perder."
                    me.paso("riesgo", txt, trabajando=False)
                    base.avisar_telefono(f"GSAM Cripto · {p['activo']} protegida", txt, "default")
                motivo = "stop" if precio <= p["stop"] else "objetivo" if precio >= p["tp"] else None
                if motivo:
                    if real:
                        p["firma_venta"] = _vender_todo(p)
                    if motivo == "stop" and p.get("be"):
                        motivo = "breakeven"
                    p["estado"], p["resultado"], p["precio_venta"], p["cerrada_ts"] = "cerrada", motivo, round(precio, 2), base.ahora()
                    gan = p["usd"] * (precio / p["precio_compra"] - 1)
                    txt = f"{et}{p['activo']} cerrada en {motivo} a ~{precio:,.0f}: {gan:+,.2f} USD."
                    me.paso("ejecucion", txt, trabajando=False)
                    base.avisar_telefono(f"GSAM Cripto · {p['activo']} cerrada", txt, "high")
        except Exception as ex:
            me.paso("ejecucion", f"{p['activo']}: no pude ejecutar el swap ({str(ex)[:90]}). Lo reintento en 30 min.", trabajando=False)
            base.avisar_telefono("GSAM Cripto · error de ejecución", f"{p['activo']}: {str(ex)[:200]}", "high")
    me.m["posiciones"] = pos
    me.guardar("vigilancia de posiciones")


def revisar_billetera():
    """Lee la billetera del bot sin operar: confirma que la conexión funciona."""
    me = Mesa()
    try:
        sal = _saldos()
        me.paso("ejecucion", f"Billetera del bot conectada ({sal['direccion'][:4]}…{sal['direccion'][-4:]}): {sal['USDC']:.2f} USDC · "
                             f"{sal['BTC']:.6f} BTC · {sal['SOL']:.4f} SOL. Modo: {'REAL' if _modo_real() else 'simulado'}.", trabajando=False)
    except Exception as ex:
        me.paso("ejecucion", f"No pude leer la billetera del bot ({str(ex)[:100]}).", trabajando=False)


def preparar():
    """Cambia el SOL de la billetera del bot a USDC (deja 0.03 SOL para comisiones). Lo lanzas tú desde GitHub Actions."""
    me = Mesa()
    try:
        sal = _saldos()
        reserva = 0.03
        cambiar = sal["SOL"] - reserva
        if cambiar < 0.01:
            me.paso("ejecucion", f"No hay SOL suficiente para cambiar (hay {sal['SOL']:.4f}, se dejan {reserva} para comisiones).", trabajando=False)
            return
        sig = _swap("So11111111111111111111111111111111111111112", USDC, int(cambiar * 1e9))
        time.sleep(3)
        nuevo = _saldos()
        txt = (f"Cambiados {cambiar:.4f} SOL a USDC (firma {sig[:10]}…). Billetera del bot: {nuevo['USDC']:.2f} USDC · "
               f"{nuevo['SOL']:.4f} SOL. Lista para operar.")
        me.paso("ejecucion", txt, trabajando=False)
        base.avisar_telefono("GSAM Cripto · billetera lista", txt, "high")
    except Exception as ex:
        me.paso("ejecucion", f"No pude cambiar SOL a USDC ({str(ex)[:100]}).", trabajando=False)


def devolver(destino):
    """Envía todo el SOL de la billetera del bot a 'destino' (una dirección tuya). Lo lanzas tú desde GitHub Actions."""
    import base64
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction
    me = Mesa()
    try:
        dest = Pubkey.from_string(destino.strip())
        kp = _llave()
        sal = _saldos()
        if sal["USDC"] > 0 or sal["BTC"] > 0:
            me.paso("ejecucion", f"La billetera del bot tiene {sal['USDC']:.2f} USDC y {sal['BTC']:.6f} BTC: pásalos a SOL o muévelos desde Phantom antes de devolver.", trabajando=False)
        lam = _rpc("getBalance", [str(kp.pubkey())])["value"] - 5000
        if lam <= 0:
            me.paso("ejecucion", "La billetera del bot no tiene SOL para devolver.", trabajando=False)
            return
        bh = _rpc("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"]
        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=lam))
        tx = Transaction([kp], Message([ix], kp.pubkey()), Hash.from_string(bh))
        sig = _rpc("sendTransaction", [base64.b64encode(bytes(tx)).decode(), {"encoding": "base64"}])
        txt = f"Devueltos {lam / 1e9:.4f} SOL a {str(dest)[:4]}…{str(dest)[-4:]} (firma {sig[:10]}…)."
        me.paso("ejecucion", txt, trabajando=False)
        base.avisar_telefono("GSAM Cripto · SOL devuelto", txt, "high")
    except Exception as ex:
        me.paso("ejecucion", f"No pude devolver el SOL ({str(ex)[:100]}).", trabajando=False)

if __name__ == "__main__":
    t = (sys.argv[1] if len(sys.argv) > 1 else "manana").lower()
    if t == "vigilar":
        vigilar_cripto()
        sys.exit(0)
    if t == "billetera":
        revisar_billetera()
        sys.exit(0)
    if t == "preparar":
        preparar()
        sys.exit(0)
    if t == "devolver":
        devolver(os.environ.get("DESTINO", ""))
        sys.exit(0)
    if t not in TURNOS:
        sys.exit("Uso: python agentes/cripto.py <manana|noche|vigilar|billetera|preparar|devolver>")
    correr(t)
