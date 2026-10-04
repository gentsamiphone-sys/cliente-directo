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
SESIONES = {"asia": "Asia (Tokio/Sídney)", "londres": "Londres", "nuevayork": "Nueva York"}


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
    if mapa["ATR14_diario"]:
        mapa["rango_hoy_vs_atr_pct"] = round((ult["h"] - ult["l"]) / mapa["ATR14_diario"] * 100)
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


def calendario():
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
        if hoy <= t.date() <= hoy + timedelta(days=1):
            sel.append({"hora_lv": t.strftime("%a %H:%M"), "evento": e.get("title"), "impacto": e.get("impact"),
                        "previsto": e.get("forecast"), "anterior": e.get("previous")})
    return {"eventos": sel[:12]}


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


# ───────── la sesión ─────────

def correr(sesion):
    nombre = SESIONES[sesion]
    me = Mesa("cio")
    me.paso("cio", f"Abriendo la mesa para la sesión de {nombre}.")

    me.paso("macro", "Revisando dólar, tasas a 10 años, VIX y calendario económico…")
    mac = macro_datos()
    cal = calendario()
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
    vix = mac.get("vix", {}).get("ultimo")
    regimen = "estrés" if isinstance(vix, (int, float)) and vix >= 25 else "elevada" if isinstance(vix, (int, float)) and vix >= 18 else "normal"
    me.paso("riesgo", f"Volatilidad {regimen} (VIX {vix}).", trabajando=False)

    me.paso("cio", "Escribiendo el brief institucional…")
    datos = {"sesion": nombre, "hora_las_vegas": datetime.now(LV).strftime("%Y-%m-%d %H:%M"), "macro": mac,
             "calendario_usd": eventos, "cot": pos, "mapas": mapas, "riesgo": riesgo, "regimen_vol": regimen,
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
7. **Nota para tu regla Ruptura EMA9 (1H)** — en 2-3 líneas: qué ruptura tendría sentido con este mapa y dónde está
   la liquidez a favor (recuerda que no se entra si la liquidez a favor está a menos de 2 ATR de 1H).
Termina con una línea: "Análisis educativo de agentes de IA. No es consejo financiero."
Usa SOLO los números de los datos; si un dato falta dilo. Máximo 450 palabras.

DATOS:
{json.dumps(datos, ensure_ascii=False)[:14000]}"""
    try:
        texto = base.gemini(prompt)
    except Exception as ex:
        me.paso("cio", f"No pude escribir el brief ({str(ex)[:70]}).", trabajando=False)
        return
    try:
        corto = base.gemini_json(f"""Del siguiente brief, devuelve JSON {{"oro": "alcista|bajista|neutral", "nq": "alcista|bajista|neutral",
"titular": "frase de máximo 90 caracteres con lo más importante de la sesión"}}.
BRIEF: {texto[:4000]}""")
    except Exception:
        corto = {"oro": "?", "nq": "?", "titular": f"Brief de {nombre}"}
    brief = {"sesion": sesion, "nombre": nombre, "ts": ahora(), "texto": texto, "sesgo": {"oro": corto.get("oro"), "nq": corto.get("nq")},
             "titular": corto.get("titular", ""), "mapas": mapas, "macro": mac, "eventos": eventos, "cot": pos}
    me.m["briefs"].insert(0, brief)
    me.paso("cio", f"Brief de {nombre} publicado: {corto.get('titular', '')}", trabajando=False)
    base.avisar_telefono(f"GSAM · {nombre}: oro {corto.get('oro')} · NQ {corto.get('nq')}",
                         (corto.get("titular", "") + "\n\n" + texto)[:3500])


if __name__ == "__main__":
    s = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if s not in SESIONES:
        sys.exit("Uso: python agentes/mesa.py <asia|londres|nuevayork>")
    correr(s)
