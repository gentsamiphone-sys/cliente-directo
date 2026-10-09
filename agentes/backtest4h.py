"""Backtest de la estrategia 4H de Gent: ruptura con fuerza de un máximo/mínimo anterior + retesteo del nivel.
Entrada límite en el nivel · stop 0.6 ATR(4H) al otro lado · objetivo en la siguiente liquidez (R:R mínimo 2).
Datos: velas de 1 hora de Yahoo (~2 años) agrupadas en 4H. Resultado en data/backtest_4h.json."""
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (GSAM backtest)"}
ACTIVOS = {"NQ": "NQ=F", "ORO": "GC=F"}
RAIZ = Path(__file__).resolve().parent.parent


def velas_1h(sim):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sim}", params={"interval": "1h", "range": "730d"}, headers=UA, timeout=60)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
        if None not in (o, h, l, c):
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out


def a_4h(v1):
    g = {}
    for v in v1:
        k = v["t"] // 14400
        if k not in g:
            g[k] = dict(v)
        else:
            x = g[k]
            x["h"], x["l"], x["c"] = max(x["h"], v["h"]), min(x["l"], v["l"]), v["c"]
    return [g[k] for k in sorted(g)]


def atr(v, n=14):
    tr = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(v, v[1:])]
    return statistics.mean(tr[-n:]) if len(tr) >= n else None


def ema(vals, n):
    k, e = 2 / (n + 1), None
    for x in vals:
        e = x if e is None else x * k + e * (1 - k)
    return e


def setups(v4, fin):
    """Setups vivos usando solo velas hasta 'fin' (sin mirar el futuro)."""
    v = v4[:fin + 1]
    a = atr(v, 14) or 1
    precio = v[-1]["c"]
    piv = []
    for i in range(max(2, len(v) - 160), len(v) - 2):
        if v[i]["h"] >= max(x["h"] for x in v[i - 2:i + 3]):
            piv.append(("max", i, v[i]["h"]))
        if v[i]["l"] <= min(x["l"] for x in v[i - 2:i + 3]):
            piv.append(("min", i, v[i]["l"]))
    out, vistos = [], set()
    for tipo, i, nivel in reversed(piv[-30:]):
        al = tipo == "max"
        j = next((k for k in range(i + 1, len(v)) if (v[k]["c"] > nivel if al else v[k]["c"] < nivel)), None)
        if j is None or len(v) - j > 30:
            continue
        fuerza = max(abs(x["c"] - x["o"]) for x in v[j:j + 3]) / a
        desp = v[j + 1:]
        if any((x["c"] < nivel - 0.3 * a) if al else (x["c"] > nivel + 0.3 * a) for x in desp):
            continue
        ext = max([v[j]["h"]] + [x["h"] for x in desp]) if al else min([v[j]["l"]] + [x["l"] for x in desp])
        stop = nivel - 0.6 * a if al else nivel + 0.6 * a
        obj = ext
        rr = abs(obj - nivel) / abs(nivel - stop)
        if rr < 2:
            mas = sorted(p for t, k, p in piv if t == "max" and p > ext) if al else sorted((p for t, k, p in piv if t == "min" and p < ext), reverse=True)
            if mas:
                obj = mas[0]
                rr = abs(obj - nivel) / abs(nivel - stop)
        dist = (precio - nivel) / a
        if rr < 2 or fuerza < 0.8 or not ((dist > -0.5) if al else (dist < 0.5)):
            continue
        clave = (al, round(nivel / a))
        if clave in vistos:
            continue
        vistos.add(clave)
        out.append({"al": al, "nivel": nivel, "stop": stop, "obj": obj, "rr": rr})
    return out


def simular(v4, filtro_tendencia=False):
    ops, i = [], 60
    while i < len(v4) - 1:
        cand = setups(v4, i)
        if filtro_tendencia and cand:
            e50 = ema([x["c"] for x in v4[:i + 1]], 50)
            cand = [c for c in cand if (v4[i]["c"] > e50) == c["al"]]
        if not cand:
            i += 1
            continue
        s = cand[0]
        # orden límite en el nivel, válida 12 velas (2 días)
        lleno = None
        for k in range(i + 1, min(i + 13, len(v4))):
            if (v4[k]["l"] <= s["nivel"]) if s["al"] else (v4[k]["h"] >= s["nivel"]):
                lleno = k
                break
        if lleno is None:
            i += 1
            continue
        res, fin = None, lleno
        for k in range(lleno, len(v4)):
            x = v4[k]
            toca_stop = x["l"] <= s["stop"] if s["al"] else x["h"] >= s["stop"]
            toca_obj = x["h"] >= s["obj"] if s["al"] else x["l"] <= s["obj"]
            if toca_stop:            # si la misma vela toca los dos, contamos pérdida (conservador)
                res, fin = -1.0, k
                break
            if toca_obj and k > lleno:
                res, fin = round(s["rr"], 2), k
                break
        if res is None:
            break
        ops.append({"fecha": datetime.fromtimestamp(v4[lleno]["t"], timezone.utc).strftime("%Y-%m-%d"),
                    "dir": "COMPRA" if s["al"] else "VENTA", "r": res})
        i = fin + 1
    return ops


def resumen(ops):
    if not ops:
        return {"operaciones": 0}
    g = [o for o in ops if o["r"] > 0]
    racha = m = 0
    for o in ops:
        racha = racha + 1 if o["r"] < 0 else 0
        m = max(m, racha)
    perd = sum(-o["r"] for o in ops if o["r"] < 0)
    meses = max(1, (datetime.fromisoformat(ops[-1]["fecha"]) - datetime.fromisoformat(ops[0]["fecha"])).days / 30.4)
    return {"operaciones": len(ops), "ganadas": len(g), "acierto_pct": round(len(g) / len(ops) * 100, 1),
            "r_promedio_ganadora": round(statistics.mean(o["r"] for o in g), 2) if g else 0,
            "r_total": round(sum(o["r"] for o in ops), 1), "r_por_operacion": round(sum(o["r"] for o in ops) / len(ops), 2),
            "profit_factor": round(sum(o["r"] for o in g) / perd, 2) if perd else None,
            "max_perdidas_seguidas": m, "operaciones_por_mes": round(len(ops) / meses, 1),
            "desde": ops[0]["fecha"], "hasta": ops[-1]["fecha"],
            "dolares_con_350": round(sum(o["r"] for o in ops) * 350)}


if __name__ == "__main__":
    out = {"generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "activos": {}}
    for nombre, sim in ACTIVOS.items():
        try:
            v4 = a_4h(velas_1h(sim))
            sin, con = simular(v4), simular(v4, True)
            out["activos"][nombre] = {"velas_4h": len(v4), "sin_filtro": resumen(sin), "con_tendencia_ema50": resumen(con),
                                      "ultimas_10": con[-10:]}
            print(nombre, json.dumps(out["activos"][nombre]["sin_filtro"]), json.dumps(out["activos"][nombre]["con_tendencia_ema50"]))
        except Exception as ex:
            out["activos"][nombre] = {"error": str(ex)[:200]}
            print(nombre, "ERROR", ex)
    (RAIZ / "data").mkdir(exist_ok=True)
    (RAIZ / "data" / "backtest_4h.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
