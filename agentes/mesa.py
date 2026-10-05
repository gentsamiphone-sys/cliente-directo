"""Mesa de inversión GSAM · Oro y NQ.

Cinco agentes con mentalidad de fondo institucional preparan el brief de cada sesión:
  macro        → dólar, tasas, volatilidad y calendario económico
  flujos       → posicionamiento de grandes jugadores (reporte COT de la CFTC)
  liquidez     → mapa de liquidez: máximos/mínimos previos, rangos de sesión, VWAP
  riesgo       → rango esperado (ATR), régimen de volatilidad, eventos
  cio          → junta todo y escribe el brief institucional de la sesión

Uso: python agentes/mesa.py <asia|londres|nuevayork>
Todo queda en data/mesa.json (la sala de trading lo lee en vivo).
Es análisis educativo; no es consejo financiero ni ejecuta operaciones.
"""
import csv
import io
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main as base  # reutiliza Gemini, ntfy y git

RAIZ = Path(__file__).resolve().parent.parent
MESA = RAIZ / "data" / "mesa.json"
LV = ZoneInfo("America/Los_Angeles")
UA = {"User-Agent": "Mozilla/5.0 (GSAM Mesa)"}
ACTIVOS = {"oro": "GC=F", "nq": "NQ=F"}
MACRO = {"dxy": "DX-Y.NYB", "us10y": "^TNX", "vix": "^VIX", "spx": "ES=F", "plata": "SI=F", "petroleo": "CL=F"}
SESIONES = {"asia": "Asia (Tokio/Sídney)", "londres": "Londres", "nuevayork": "Nueva York", "semana": "Plan de la semana"}


def ahora():
    return base.ahora()


# ───────── estado de la mesa ─────────

class Mesa:
    def __init__(self, agente):
        self.agente = agente
        if base.EN_ACTIONS:
            base.git("pull", "--rebase", "--quiet", check=False)
        try:
            self.m = json.loads(MESA.read_text(encoding="utf-8"))
        except Exception:
            self.m = {"agentes": {}, "actividad": [], "briefs": [], "cot_previo": {}}

    def guardar(self, msg):
        self.m["actividad"] = self.m["actividad"][:200]
        self.m["briefs"] = self.m["briefs"][:60]
        MESA.write_text(json.dumps(self.m, ensure_ascii=False, indent=1), encoding="utf-8")
        if not base.EN_ACTIONS:
            return
        base.git("add", "data/mesa.json")
        if base.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        base.git("commit", "-q", "-m", f"[mesa:{self.agente}] {msg[:60]}")
        for i in range(4):
            if base.git("push", "-q", check=False).returncode == 0:
                return
            base.git("pull", "--rebase", "-X", "theirs", "--quiet", check=False)
            time.sleep(2 + 2 * i)

    def paso(self, agente, texto, trabajando=True):
        print(f"[{agente}] {texto}")
        self.agente = agente
        self.m["actividad"].insert(0, {"agente": agente, "texto": texto, "ts": ahora()})
        a = self.m["agentes"].setdefault(agente, {})
        if trabajando:
            a.update(estado="trabajando", tarea=texto, actualizado=ahora())
        else:
            a.update(estado="descansando", tarea="", ultimo_resultado=texto, actualizado=ahora())
        self.guardar(texto)


# ───────── datos de mercado (gratis) ─────────

def yahoo(simbolo, intervalo="1d", rango="3mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
    r = requests.get(url, params={"interval": intervalo, "range": rango}, headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    velas = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        velas.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    return velas


def atr(velas, n=14):
    trs = []
    for a, b in zip(velas, velas[1:]):
        trs.append(max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])))
    return statistics.mean(trs[-n:]) if len(trs) >= n else None


def ema(vals, n):
    k, e = 2 / (n + 1), None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
    return e


def rango_sesion(horas, desde_utc, hasta_utc, dia):
    """Máximo/mínimo de las velas 1H entre dos horas UTC del día indicado (date UTC)."""
    sel = []
    for v in horas:
        t = datetime.fromtimestamp(v["t"], timezone.utc)
        h = t.hour
        dentro = (desde_utc <= h < hasta_utc) if desde_utc < hasta_utc else (h >= desde_utc or h < hasta_utc)
        dia_sesion = t.date() + timedelta(days=1) if desde_utc > hasta_utc and h >= desde_utc else t.date()
        if dentro and dia_sesion == dia:
            sel.append(v)
    if not sel:
        return None
    return {"max": round(max(v["h"] for v in sel), 2), "min": round(min(v["l"] for v in sel), 2)}


def vwap_sesion(horas, dia):
    sel = [v for v in horas if datetime.fromtimestamp(v["t"], timezone.utc).date() == dia and v["v"]]
    if not sel:
        return None
    pv = sum((v["h"] + v["l"] + v["c"]) / 3 * v["v"] for v in sel)
    vol = sum(v["v"] for v in sel)
    return pv / vol if vol else None


def mapa_activo(simbolo):
    d = yahoo(simbolo, "1d", "6mo")
    h = yahoo(simbolo, "1h", "10d")
    ult, prev = d[-1], d[-2]
    hoy = datetime.now(timezone.utc).date()
    ayer = datetime.fromtimestamp(prev["t"], timezone.utc).date()
    semana = d[-6:-1]
    cierres = [v["c"] for v in d]
    mapa = {
        "precio": round(h[-1]["c"] if h else ult["c"], 2),
        "cambio_dia_pct": round((ult["c"] / prev["c"] - 1) * 100, 2),
        "PDH": round(prev["h"], 2), "PDL": round(prev["l"], 2), "PDC": round(prev["c"], 2),
        "PWH": round(max(v["h"] for v in semana), 2), "PWL": round(min(v["l"] for v in semana), 2),
        "ATR14_diario": round(atr(d) or 0, 2),
        "ATR14_1h": round(atr(h) or 0, 2),
        "EMA20_diaria": round(ema(cierres, 20), 2), "EMA50_diaria": round(ema(cierres, 50), 2),
        "EMA200_diaria": round(ema(cierres, 200), 2) if len(cierres) >= 120 else None,
        "max_20d": round(max(v["h"] for v in d[-20:]), 2), "min_20d": round(min(v["l"] for v in d[-20:]), 2),
        "asia_hoy": rango_sesion(h, 23, 7, hoy) or rango_sesion(h, 23, 7, ayer),
        "londres_hoy": rango_sesion(h, 7, 13, hoy),
        "ny_ayer": rango_sesion(h, 13, 20, ayer),
        "vwap_hoy": round(vwap_sesion(h, hoy) or 0, 2) or None,
    }
    # rango realizado vs esperado
    # día de futuros: empieza a las 22:00 UTC (apertura de Globex); se mide solo lo que va de HOY
    ahora_u = datetime.now(timezone.utc)
    inicio = ahora_u.replace(hour=22, minute=0, second=0, microsecond=0)
    if inicio > ahora_u:
        inicio -= timedelta(days=1)
    hoy_v = [v for v in h if v["t"] >= inicio.timestamp()]
    mapa["rango_hoy"] = {"max": round(max(v["h"] for v in hoy_v), 2), "min": round(min(v["l"] for v in hoy_v), 2)} if hoy_v else None
    if mapa["ATR14_diario"]:
        mapa["rango_hoy_vs_atr_pct"] = round((max(v["h"] for v in hoy_v) - min(v["l"] for v in hoy_v)) / mapa["ATR14_diario"] * 100) if hoy_v else 0
    return mapa


def macro_datos():
    out = {}
    for k, s in MACRO.items():
        try:
            d = yahoo(s, "1d", "1mo")
            out[k] = {"ultimo": round(d[-1]["c"], 3), "cambio_1d_pct": round((d[-1]["c"] / d[-2]["c"] - 1) * 100, 2),
                      "cambio_5d_pct": round((d[-1]["c"] / d[-6]["c"] - 1) * 100, 2)}
        except Exception as ex:
            out[k] = {"error": str(ex)[:60]}
    return out


def calendario(dias=1):
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=UA, timeout=30)
        eventos = r.json()
    except Exception as ex:
        return {"error": str(ex)[:80], "eventos": []}
    hoy = datetime.now(LV).date()
    sel = []
    for e in eventos:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(LV)
        except Exception:
            continue
        if hoy <= t.date() <= hoy + timedelta(days=dias):
            sel.append({"hora_lv": t.strftime("%a %H:%M"), "evento": e.get("title"), "impacto": e.get("impact"),
                        "previsto": e.get("forecast"), "anterior": e.get("previous")})
    return {"eventos": sel[:40 if dias > 1 else 12]}


def _cot_tabla(url):
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return list(csv.reader(io.StringIO(r.text)))


def _num(x):
    try:
        return int(str(x).strip())
    except Exception:
        return None


def cot():
    """Posicionamiento semanal (CFTC). Oro: Managed Money (fondos). NQ: Asset Managers y Leveraged Funds."""
    out = {}
    try:
        for fila in _cot_tabla("https://www.cftc.gov/dea/newcot/f_disagg.txt"):
            if fila and fila[0].strip().startswith("GOLD - COMMODITY EXCHANGE"):
                oi, mm_l, mm_s = _num(fila[7]), _num(fila[13]), _num(fila[14])
                out["oro"] = {"fecha": fila[2].strip(), "interes_abierto": oi, "fondos_largos": mm_l,
                              "fondos_cortos": mm_s, "fondos_neto": (mm_l or 0) - (mm_s or 0)}
                break
    except Exception as ex:
        out["oro"] = {"error": str(ex)[:80]}
    try:
        for fila in _cot_tabla("https://www.cftc.gov/dea/newcot/FinFutWk.txt"):
            nombre = fila[0].strip() if fila else ""
            if nombre.startswith("NASDAQ MINI") or nombre.startswith("NASDAQ-100 STOCK INDEX (MINI)"):
                oi = _num(fila[7])
                am_l, am_s, lev_l, lev_s = _num(fila[11]), _num(fila[12]), _num(fila[14]), _num(fila[15])
                out["nq"] = {"fecha": fila[2].strip(), "interes_abierto": oi,
                             "asset_managers_neto": (am_l or 0) - (am_s or 0),
                             "leveraged_funds_neto": (lev_l or 0) - (lev_s or 0)}
                break
    except Exception as ex:
        out["nq"] = {"error": str(ex)[:80]}
    return out


def titulares():
    try:
        r = requests.get("https://feeds.finance.yahoo.com/rss/2.0/headline", params={"s": "GC=F,NQ=F,^IXIC", "region": "US", "lang": "en-US"},
                         headers=UA, timeout=30)
        import re
        return re.findall(r"<item>.*?<title>(.*?)</title>", r.text, re.S)[:10]
    except Exception:
        return []


def vix_confluencia():
    """VIX vs NQ: estructura de volatilidad, correlación y divergencias (lo que mira una mesa antes de tomar riesgo en NQ)."""
    vd, vh = yahoo("^VIX", "1d", "3mo"), yahoo("^VIX", "1h", "10d")
    nh = yahoo("NQ=F", "1h", "10d")
    out = {"vix": round(vd[-1]["c"], 2), "cambio_1d_pct": round((vd[-1]["c"] / vd[-2]["c"] - 1) * 100, 2),
           "vix_PDH": round(vd[-2]["h"], 2), "vix_PDL": round(vd[-2]["l"], 2),
           "vix_media20": round(statistics.mean(v["c"] for v in vd[-20:]), 2),
           "vix_max_20d": round(max(v["h"] for v in vd[-20:]), 2), "vix_min_20d": round(min(v["l"] for v in vd[-20:]), 2)}
    try:
        v3 = yahoo("^VIX3M", "1d", "1mo")
        ratio = vd[-1]["c"] / v3[-1]["c"]
        out["vix_vs_vix3m"] = round(ratio, 3)
        out["estructura"] = "backwardation (estrés: cobertura cara a corto plazo)" if ratio > 1 else "contango (calma normal)"
    except Exception:
        pass
    # correlación de retornos 1H en las últimas 48 velas comunes
    vm = {v["t"]: v["c"] for v in vh}
    pares = [(n["c"], vm[n["t"]]) for n in nh if n["t"] in vm][-49:]
    if len(pares) > 10:
        rn = [b[0] / a[0] - 1 for a, b in zip(pares, pares[1:])]
        rv = [b[1] / a[1] - 1 for a, b in zip(pares, pares[1:])]
        try:
            out["correlacion_48h"] = round(statistics.correlation(rn, rv), 2)
        except Exception:
            pass
        # últimas 6 horas: ¿confirman o divergen?
        n6 = pares[-1][0] / pares[-7][0] - 1 if len(pares) > 7 else 0
        v6 = pares[-1][1] / pares[-7][1] - 1 if len(pares) > 7 else 0
        out["nq_6h_pct"], out["vix_6h_pct"] = round(n6 * 100, 2), round(v6 * 100, 2)
        if n6 > 0 and v6 < 0:
            out["lectura"] = "CONFIRMA ALCISTA: NQ sube y el VIX baja (apetito de riesgo real)"
        elif n6 < 0 and v6 > 0:
            out["lectura"] = "CONFIRMA BAJISTA: NQ baja y el VIX sube (se compra protección)"
        elif n6 > 0 and v6 > 0:
            out["lectura"] = "DIVERGENCIA: NQ sube pero el VIX también sube (las instituciones se cubren; subida sospechosa)"
        elif n6 < 0 and v6 < 0:
            out["lectura"] = "DIVERGENCIA: NQ baja pero el VIX también baja (caída sin miedo; posible barrida para comprar)"
        else:
            out["lectura"] = "SIN SEÑAL clara"
    return out


# ───────── auditor: revisa cómo salió cada entrada ─────────

import re as _re


def _n(x):
    m = _re.findall(r"\d[\d,]*\.?\d*", str(x or ""))
    vals = [float(v.replace(",", "")) for v in m]
    return sum(vals) / len(vals) if vals else None


def auditar(me):
    """Para cada entrada propuesta: ¿se activó? ¿tocó primero el stop o el objetivo? Guarda el resultado."""
    velas = {}
    for b in me.m.get("briefs", []):
        for e in b.get("entradas") or []:
            if e.get("resultado") or str(e.get("direccion", "")).upper().startswith("SIN"):
                continue
            act = "oro" if "ORO" in str(e.get("activo", "")).upper() else "nq"
            ent, sl, tp1, tp2 = _n(e.get("zona")), _n(e.get("stop")), _n(e.get("tp1")), _n(e.get("tp2"))
            if None in (ent, sl, tp1):
                e["resultado"] = "sin datos"
                continue
            if act not in velas:
                velas[act] = yahoo(ACTIVOS[act], "1h", "1mo")
            t0 = datetime.fromisoformat(b["ts"].replace("Z", "+00:00")).timestamp()
            compra = str(e.get("direccion", "")).upper().startswith("COMPRA")
            activa, res = False, None
            for v in velas[act]:
                if v["t"] < t0:
                    continue
                if v["t"] > t0 + 30 * 3600:
                    break
                if not activa:
                    if v["l"] <= ent <= v["h"]:
                        activa = True
                    elif (v["t"] - t0) > 12 * 3600:
                        res = "no se activó"
                        break
                    else:
                        continue
                toca_sl = v["l"] <= sl if compra else v["h"] >= sl
                toca_tp2 = tp2 and (v["h"] >= tp2 if compra else v["l"] <= tp2)
                toca_tp1 = v["h"] >= tp1 if compra else v["l"] <= tp1
                if toca_sl:
                    res = "stop"
                    break
                if toca_tp2:
                    res = "TP2"
                    break
                if toca_tp1 and not e.get("tp1_tocado"):
                    e["tp1_tocado"] = True
            if res is None and (datetime.now(timezone.utc).timestamp() - t0) > 30 * 3600:
                res = "TP1" if e.get("tp1_tocado") else ("cerrada sin objetivo" if activa else "no se activó")
            if res:
                riesgo = abs(ent - sl) or 1
                e["resultado"] = res
                e["r"] = {"stop": -1.0, "TP2": round(abs(tp2 - ent) / riesgo, 2) if tp2 else 0, "TP1": round(abs(tp1 - ent) / riesgo, 2)}.get(res, 0.0)
    # estadísticas
    cerradas = [(b, e) for b in me.m.get("briefs", []) for e in (b.get("entradas") or [])
                if e.get("resultado") in ("stop", "TP1", "TP2", "cerrada sin objetivo")]
    def resumen(lista):
        if not lista:
            return None
        g = sum(1 for _, e in lista if e["resultado"] in ("TP1", "TP2"))
        return {"operaciones": len(lista), "ganadas": g, "acierto_pct": round(g / len(lista) * 100),
                "r_total": round(sum(e.get("r", 0) for _, e in lista), 2)}
    stats = {"total": resumen(cerradas)}
    for act in ("ORO", "NQ"):
        for ses in SESIONES:
            for d in ("COMPRA", "VENTA"):
                sub = [(b, e) for b, e in cerradas if act in str(e.get("activo", "")).upper() and b.get("sesion") == ses
                       and str(e.get("direccion", "")).upper().startswith(d)]
                if sub:
                    stats[f"{act} {d} {ses}"] = resumen(sub)
    me.m["estadisticas"] = stats
    return stats


# ───────── la sesión ─────────

SEMANAL = """ESTE ES EL PLAN DE LA SEMANA (domingo, antes de que abra el mercado). Cambia el enfoque:
- En "Lo que mueve hoy" pon los eventos de TODA la semana, día por día con hora de Las Vegas, y marca los de alto impacto.
- En "Mapa de liquidez" usa niveles semanales: máximo/mínimo de la semana pasada (PWH/PWL), cierre semanal y los niveles diarios clave.
- En "Escenarios" piensa en la semana completa: qué tendría que pasar para una semana alcista o bajista y qué días son los peligrosos.
- En "Plan de entrada" NO des entradas para ejecutar: escribe SIN ENTRADA en ambos activos con el motivo
  "las entradas se definen en el brief de cada sesión". En su lugar da las ZONAS de la semana donde la mesa buscaría compras y ventas.
- Termina con un "Resumen de la semana" de 3 líneas: sesgo, días clave y la idea principal."""


def correr(sesion):
    nombre = SESIONES[sesion]
    me = Mesa("cio")
    me.paso("cio", f"Abriendo la mesa para la sesión de {nombre}.")
    me.paso("auditor", "Revisando cómo salieron las entradas anteriores…")
    try:
        stats = auditar(me)
        t = stats.get("total")
        me.paso("auditor", (f"Historial: {t['operaciones']} operaciones, {t['acierto_pct']}% de acierto, {t['r_total']:+} R."
                            if t else "Todavía no hay operaciones cerradas para medir."), trabajando=False)
    except Exception as ex:
        stats = {}
        me.paso("auditor", f"No pude auditar ({str(ex)[:60]}).", trabajando=False)

    me.paso("macro", "Revisando dólar, tasas a 10 años, VIX y calendario económico…")
    mac = macro_datos()
    cal = calendario(6 if sesion == "semana" else 1)
    eventos = cal.get("eventos", [])
    me.paso("macro", f"DXY {mac.get('dxy', {}).get('cambio_1d_pct', '?')}% · 10Y {mac.get('us10y', {}).get('ultimo', '?')} · VIX {mac.get('vix', {}).get('ultimo', '?')} · {len(eventos)} eventos USD en el radar.", trabajando=False)

    me.paso("flujos", "Leyendo el reporte COT de la CFTC (posición de fondos)…")
    pos = cot()
    previo = me.m.get("cot_previo", {})
    for k in ("oro", "nq"):
        if k in pos and "error" not in pos[k] and previo.get(k, {}).get("fecha") != pos[k].get("fecha"):
            pos[k]["semana_anterior"] = previo.get(k)
            me.m.setdefault("cot_previo", {})[k] = {kk: vv for kk, vv in pos[k].items() if kk != "semana_anterior"}
    oro_neto = pos.get("oro", {}).get("fondos_neto")
    me.paso("flujos", f"Fondos en oro: neto {oro_neto:+,} contratos." if isinstance(oro_neto, int) else "COT leído.", trabajando=False)

    me.paso("liquidez", "Marcando liquidez: PDH/PDL, PWH/PWL, rangos de Asia y Londres, VWAP…")
    mapas = {}
    for k, s in ACTIVOS.items():
        try:
            mapas[k] = mapa_activo(s)
        except Exception as ex:
            mapas[k] = {"error": str(ex)[:80]}
    me.paso("liquidez", "Mapa listo: oro {} · NQ {}.".format(mapas.get("oro", {}).get("precio", "?"), mapas.get("nq", {}).get("precio", "?")), trabajando=False)

    me.paso("riesgo", "Midiendo volatilidad y rango esperado del día…")
    riesgo = {}
    for k, m in mapas.items():
        if "ATR14_diario" in m:
            riesgo[k] = {"rango_esperado": f"{round(m['precio'] - m['ATR14_diario'] / 2, 2)} – {round(m['precio'] + m['ATR14_diario'] / 2, 2)}",
                         "consumido_pct": m.get("rango_hoy_vs_atr_pct")}
    me.paso("riesgo", "Cruzando el VIX con el NQ: estructura, correlación y divergencias…")
    try:
        vixc = vix_confluencia()
    except Exception as ex:
        vixc = {"error": str(ex)[:80]}
    me.paso("riesgo", "VIX/NQ: " + vixc.get("lectura", "sin datos"))
    vix = vixc.get("vix") or mac.get("vix", {}).get("ultimo")
    regimen = "estrés" if isinstance(vix, (int, float)) and vix >= 25 else "elevada" if isinstance(vix, (int, float)) and vix >= 18 else "normal"
    me.paso("riesgo", f"Volatilidad {regimen} (VIX {vix}).", trabajando=False)

    me.paso("cio", "Escribiendo el brief institucional…")
    datos = {"sesion": nombre, "hora_las_vegas": datetime.now(LV).strftime("%Y-%m-%d %H:%M"), "macro": mac,
             "calendario_usd": eventos, "cot": pos, "mapas": mapas, "riesgo": riesgo, "regimen_vol": regimen, "vix_nq": vixc, "historial_de_la_mesa": stats,
             "titulares": titulares()}
    prompt = f"""Eres el CIO (director de inversiones) de GSAM Capital, un fondo macro que opera oro (futuro GC / XAUUSD)
y el Nasdaq 100 (futuro NQ). Piensas como una institución, no como un trader minorista:
- Partes del contexto macro (dólar, tasas reales, apetito de riesgo, volatilidad) y del flujo/posicionamiento (COT).
- Ves el gráfico como un mapa de liquidez: dónde están los stops de los minoristas (máximos y mínimos previos,
  rangos de Asia y Londres, máximos/mínimos iguales), adónde necesita ir el precio para llenar órdenes grandes,
  y dónde está el valor justo (VWAP, medias diarias).
- No persigues precio: esperas que el mercado barra liquidez y luego confirme. Gestionas riesgo y tamaño primero.
- Hablas en escenarios con disparador e invalidación, nunca en certezas.

Escribe el BRIEF DE LA SESIÓN DE {nombre.upper()} en español, claro y directo, con estos títulos exactos:
1. **Sesgo institucional** — una línea para ORO y una para NQ (alcista / bajista / neutral) y la razón principal.
2. **Lo que mueve hoy** — macro, dólar, tasas, VIX y los eventos del calendario con hora de Las Vegas.
3. **Posicionamiento** — qué dicen los datos COT y qué implica (¿gente atrapada? ¿espacio para seguir?).
4. **Mapa de liquidez** — para cada activo, los niveles clave con su número: liquidez por arriba, por abajo,
   valor justo, y cuál es el imán más probable de la sesión.
5. **Escenarios** — para cada activo: Escenario A y B, cada uno con disparador, objetivo e invalidación (con números).
6. **Riesgo** — rango esperado (ATR), cuánto ya se consumió, horas peligrosas y qué haría la mesa con el tamaño.
7. **VIX y NQ (confluencia)** — nivel del VIX y sus niveles (PDH/PDL del VIX, media 20), estructura VIX/VIX3M,
   correlación con NQ y la lectura de las últimas 6 horas. Di claramente si el VIX CONFIRMA o CONTRADICE el sesgo de NQ,
   y qué nivel del VIX invalidaría el escenario alcista de NQ (por ejemplo, si rompe su máximo de ayer).
8. **Plan de entrada institucional (Gent ejecuta)** — para ORO y para NQ, en este formato exacto:
   - Dirección: COMPRA / VENTA / SIN ENTRADA
   - Zona de entrada: precio o rango (donde una institución pondría su orden límite: tras barrer liquidez, en valor justo o en la última vela contraria)
   - Confirmación: qué tiene que pasar antes de entrar (barrida + cierre de vela 1H de regreso, VIX confirmando, etc.)
   - Stop: más allá de la liquidez que protege la idea (con número)
   - TP1 / TP2: en la liquidez opuesta (con números)
   - R:R: relación riesgo/beneficio al TP2
   - Cancelar si: qué invalida la entrada antes de activarse (hora, noticia, nivel)
   - Confluencias (0 a 5): suma 1 por cada una que se cumpla a favor de la idea: (1) sesgo macro/dólar/tasas,
     (2) posicionamiento COT, (3) VIX confirma (para NQ) o dólar/tasas reales confirman (para oro),
     (4) la entrada está en una zona donde ya se barrió liquidez o en valor justo, (5) el historial de la mesa para ese
     activo + dirección + sesión no es negativo. Escribe "Confluencias: X/5" y cuáles.
   ALTA PROBABILIDAD SOLAMENTE: si una idea tiene menos de 4/5 confluencias, la respuesta es SIN ENTRADA.
   Si el historial muestra que una combinación (activo + dirección + sesión) tiene menos de 45% de acierto con 5 o más
   operaciones, no la propongas. Es mejor no operar que operar una idea mediocre.
   Reglas de la mesa: solo propones entrada si el R:R al TP2 es 2 o más; si hay noticia de impacto alto en los próximos
   30 minutos o el ATR del día ya está consumido más del 100%, la respuesta es SIN ENTRADA y explicas por qué.
   Una sola idea por activo. Nunca entres persiguiendo el precio.
9. **Nota para tu regla Ruptura EMA9 (1H)** — en 2-3 líneas: qué ruptura tendría sentido con este mapa y dónde está
   la liquidez a favor (recuerda que no se entra si la liquidez a favor está a menos de 2 ATR de 1H).
Termina con una línea: "Análisis educativo de agentes de IA. No es consejo financiero."
Usa SOLO los números de los datos; si un dato falta dilo. Máximo 650 palabras.

{SEMANAL if sesion == "semana" else ""}
DATOS:
{json.dumps(datos, ensure_ascii=False)[:14000]}"""
    try:
        texto = base.gemini(prompt)
    except Exception as ex:
        me.paso("cio", f"No pude escribir el brief ({str(ex)[:70]}).", trabajando=False)
        return
    try:
        corto = base.gemini_json(f"""Del siguiente brief, devuelve JSON {{"oro": "alcista|bajista|neutral", "nq": "alcista|bajista|neutral",
"titular": "frase de máximo 90 caracteres con lo más importante de la sesión",
"entradas": [{{"activo": "ORO|NQ", "direccion": "COMPRA|VENTA|SIN ENTRADA", "zona": "precio o rango", "confirmacion": "texto corto",
"stop": "número", "tp1": "número", "tp2": "número", "rr": "número", "confluencias": "número 0-5", "cancelar": "texto corto", "motivo": "si es SIN ENTRADA, por qué"}}]}}.
Copia los números tal cual aparecen en el brief.
BRIEF: {texto[:6000]}""")
    except Exception:
        corto = {"oro": "?", "nq": "?", "titular": f"Brief de {nombre}", "entradas": []}
    for e in corto.get("entradas", []) or []:
        try:
            conf = int(_n(e.get("confluencias")) or 0)
        except Exception:
            conf = 0
        if not str(e.get("direccion", "")).upper().startswith("SIN") and conf < 4:
            e["motivo"] = f"Solo {conf}/5 confluencias: no es de alta probabilidad."
            e["direccion"] = "SIN ENTRADA"
    brief = {"sesion": sesion, "nombre": nombre, "ts": ahora(), "texto": texto, "sesgo": {"oro": corto.get("oro"), "nq": corto.get("nq")},
             "titular": corto.get("titular", ""), "entradas": corto.get("entradas", []), "mapas": mapas, "vix_nq": vixc, "macro": mac, "eventos": eventos, "cot": pos}
    me.m["briefs"].insert(0, brief)
    me.paso("cio", f"Brief de {nombre} publicado: {corto.get('titular', '')}", trabajando=False)
    lineas = []
    for e in corto.get("entradas", []) or []:
        if str(e.get("direccion", "")).upper().startswith("SIN"):
            lineas.append(f"{e.get('activo')}: SIN ENTRADA — {e.get('motivo', '')}")
        else:
            lineas.append(f"{e.get('activo')} {e.get('direccion')} en {e.get('zona')} · SL {e.get('stop')} · TP1 {e.get('tp1')} · TP2 {e.get('tp2')} · R:R {e.get('rr')}\n  Confirmación: {e.get('confirmacion')}")
    base.avisar_telefono(f"GSAM · {nombre}: oro {corto.get('oro')} · NQ {corto.get('nq')}",
                         ("\n".join(lineas) + "\n\n" + corto.get("titular", "") + "\n\n" + texto)[:3800])


# ───────── vigilante: mira el precio mientras el mercado está abierto (sin IA, gratis) ─────────

def mercado_abierto(t=None):
    """Globex (oro y NQ): domingo 22:00 UTC → viernes 21:00 UTC, con pausa diaria 21:00–22:00 UTC."""
    t = t or datetime.now(timezone.utc)
    d, h = t.weekday(), t.hour  # lunes=0 … domingo=6
    if d == 5 or (d == 4 and h >= 21) or (d == 6 and h < 22):
        return False
    return h != 21


def _rango(x):
    vals = [float(v.replace(",", "")) for v in _re.findall(r"\d[\d,]*\.?\d*", str(x or ""))]
    vals = [v for v in vals if v > 50]
    return (min(vals), max(vals)) if vals else (None, None)


def vigilar():
    if not mercado_abierto():
        print("Mercado cerrado; el vigilante descansa.")
        return
    me = Mesa("vigilante")
    vivo = {}
    for k, sim in {**ACTIVOS, "vix": MACRO["vix"]}.items():
        try:
            v = yahoo(sim, "5m", "1d")
            vivo[k] = {"precio": round(v[-1]["c"], 2), "velas": v}
        except Exception as ex:
            print("sin precio", k, ex)
    me.m["en_vivo"] = {k: x["precio"] for k, x in vivo.items()} | {"ts": ahora()}
    b = next((x for x in me.m.get("briefs", []) if x.get("entradas")), None)
    avisos = []
    if b:
        t0 = datetime.fromisoformat(b["ts"].replace("Z", "+00:00")).timestamp()
        for e in b["entradas"]:
            if str(e.get("direccion", "")).upper().startswith("SIN") or e.get("resultado"):
                continue
            act = "oro" if "ORO" in str(e.get("activo", "")).upper() else "nq"
            if act not in vivo:
                continue
            lo, hi = _rango(e.get("zona"))
            sl, tp1, tp2 = _n(e.get("stop")), _n(e.get("tp1")), _n(e.get("tp2"))
            if lo is None or sl is None:
                continue
            compra = str(e.get("direccion", "")).upper().startswith("COMPRA")
            velas = [v for v in vivo[act]["velas"] if v["t"] >= t0]
            precio = vivo[act]["precio"]
            nom = f"{e.get('activo')} {e.get('direccion')}"
            est = e.setdefault("vivo", "esperando")
            if est == "esperando":
                if any((v["l"] <= sl) if compra else (v["h"] >= sl) for v in velas):
                    e["vivo"] = "cancelada"
                    avisos.append((f"❌ {nom}: cancelada", f"El precio llegó al stop ({sl}) sin activarse. No entres."))
                elif any(v["l"] <= hi and v["h"] >= lo for v in velas):
                    e["vivo"] = "en_zona"
                    avisos.append((f"📍 {nom}: llegó a la zona {lo}–{hi}",
                                   f"Precio {precio}. Todavía NO entres: espera la confirmación → {e.get('confirmacion', '')}\n"
                                   f"Stop {sl} · TP1 {tp1} · TP2 {tp2}"))
            elif est == "en_zona":
                # confirmación: la última vela de 1 hora cerrada termina a favor y de regreso fuera del lado malo de la zona
                try:
                    h1 = [v for v in yahoo(ACTIVOS[act], "60m", "2d") if v["t"] >= t0][:-1]
                except Exception:
                    h1 = []
                if h1:
                    u = h1[-1]
                    ok = (u["c"] > u["o"] and u["c"] >= lo) if compra else (u["c"] < u["o"] and u["c"] <= hi)
                    if ok:
                        e["vivo"] = "confirmada"
                        avisos.append((f"✅ {nom}: CONFIRMÓ", f"Vela 1H cerró a favor en {round(u['c'], 2)}. Precio {precio}.\n"
                                       f"Si entras: stop {sl} · TP1 {tp1} · TP2 {tp2}. Tú decides."))
                if any((v["l"] <= sl) if compra else (v["h"] >= sl) for v in velas[-3:]):
                    e["vivo"] = "cancelada"
                    avisos.append((f"❌ {nom}: tocó el stop", f"No confirmó y llegó a {sl}. Idea cancelada."))
            elif est == "confirmada":
                ult = velas[-3:]
                if any((v["l"] <= sl) if compra else (v["h"] >= sl) for v in ult):
                    e["vivo"] = "stop"
                    avisos.append((f"🛑 {nom}: STOP", f"Tocó {sl}."))
                elif tp2 and any((v["h"] >= tp2) if compra else (v["l"] <= tp2) for v in ult):
                    e["vivo"] = "tp2"
                    avisos.append((f"🎯 {nom}: TP2", f"Llegó a {tp2}. Objetivo completo."))
                elif tp1 and not e.get("aviso_tp1") and any((v["h"] >= tp1) if compra else (v["l"] <= tp1) for v in ult):
                    e["aviso_tp1"] = True
                    avisos.append((f"🎯 {nom}: TP1", f"Llegó a {tp1}. Considera asegurar parte y mover el stop a la entrada."))
    p = me.m["en_vivo"]
    resumen = f"Oro {p.get('oro', '—')} · NQ {p.get('nq', '—')} · VIX {p.get('vix', '—')}"
    for t, txt in avisos:
        base.avisar_telefono("GSAM · " + t, txt, "high")
    me.paso("vigilante", (" | ".join(t for t, _ in avisos) + " · " if avisos else "") + resumen, trabajando=False)


if __name__ == "__main__":
    s = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if s == "vigilar":
        vigilar()
    elif s in SESIONES:
        correr(s)
    else:
        sys.exit("Uso: python agentes/mesa.py <asia|londres|nuevayork|vigilar>")
