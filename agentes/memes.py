"""Mesa de memecoins · copia inteligente de traders top (solo señales; tú ejecutas en fomo).

Agentes:
  vigia      → lee en la blockchain de Solana las compras/ventas recientes de las wallets seguidas
  senal      → cuando 2+ wallets top compran la misma moneda en pocas horas, arma la señal
  seguridad  → revisa liquidez, market cap, edad y riesgo de rug (DexScreener + RugCheck)
  auditor    → mide cómo le fue a cada señal (+1h, +6h, +24h) y a cada wallet

Uso: python agentes/memes.py
Datos en data/memes.json. No compra ni vende nada: solo avisa.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main as base

RAIZ = Path(__file__).resolve().parent.parent
MEMES = RAIZ / "data" / "memes.json"
RPCS = ["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"]
IGNORAR = {"So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
           "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
VENTANA_H = 6          # horas para juntar compras de varias wallets
MIN_WALLETS = 2
FILTROS = {"liquidez_min": 20000, "mcap_min": 100000, "mcap_max": 80_000_000, "edad_min_min": 30}


def ahora_ts():
    return time.time()


def iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Estado:
    def __init__(self):
        if base.EN_ACTIONS:
            base.git("pull", "--rebase", "--quiet", check=False)
        self.m = json.loads(MEMES.read_text(encoding="utf-8"))

    def guardar(self, msg):
        self.m["trades"] = self.m["trades"][:600]
        self.m["actividad"] = self.m["actividad"][:150]
        self.m["senales"] = self.m["senales"][:120]
        MEMES.write_text(json.dumps(self.m, ensure_ascii=False, indent=1), encoding="utf-8")
        if not base.EN_ACTIONS:
            return
        base.git("add", "data/memes.json")
        if base.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        base.git("commit", "-q", "-m", f"[memes] {msg[:60]}")
        for i in range(4):
            if base.git("push", "-q", check=False).returncode == 0:
                return
            base.git("pull", "--rebase", "-X", "theirs", "--quiet", check=False)
            time.sleep(2 + 2 * i)

    def paso(self, agente, texto, trabajando=True):
        print(f"[{agente}] {texto}")
        self.m["actividad"].insert(0, {"agente": agente, "texto": texto, "ts": iso()})
        a = self.m["agentes"].setdefault(agente, {})
        if trabajando:
            a.update(estado="trabajando", tarea=texto, actualizado=iso())
        else:
            a.update(estado="descansando", tarea="", ultimo_resultado=texto, actualizado=iso())


# ───────── Solana (gratis, sin llaves) ─────────

def rpc(metodo, params):
    ultimo = None
    for url in RPCS:
        for i in range(3):
            try:
                r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params}, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 + 2 * i)
                    continue
                j = r.json()
                if "error" in j:
                    ultimo = j["error"]
                    break
                return j.get("result")
            except Exception as ex:
                ultimo = ex
                time.sleep(1)
    raise RuntimeError(f"RPC {metodo}: {ultimo}")


def movimientos(wallet, desde_sig, max_tx=8):
    """Compras/ventas nuevas de una wallet desde la última firma vista."""
    firmas = rpc("getSignaturesForAddress", [wallet, {"limit": 20}]) or []
    nuevas = []
    for f in firmas:
        if f["signature"] == desde_sig:
            break
        if f.get("err") is None:
            nuevas.append(f)
    salida = []
    for f in nuevas[:max_tx]:
        tx = rpc("getTransaction", [f["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = {(b["mint"]): float(b["uiTokenAmount"]["uiAmount"] or 0) for b in meta.get("preTokenBalances", []) if b.get("owner") == wallet}
        post = {(b["mint"]): float(b["uiTokenAmount"]["uiAmount"] or 0) for b in meta.get("postTokenBalances", []) if b.get("owner") == wallet}
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
        sol = 0.0
        if wallet in keys:
            i = keys.index(wallet)
            sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
        for mint in set(pre) | set(post):
            if mint in IGNORAR:
                continue
            d = post.get(mint, 0) - pre.get(mint, 0)
            if abs(d) <= 0:
                continue
            salida.append({"wallet": wallet, "mint": mint, "lado": "compra" if d > 0 else "venta",
                           "sol": round(abs(sol), 3), "ts": f.get("blockTime") or int(ahora_ts()), "sig": f["signature"]})
    return (firmas[0]["signature"] if firmas else desde_sig), salida


# ───────── datos del token (gratis) ─────────

def dexscreener(mint):
    r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=20)
    pares = [p for p in (r.json().get("pairs") or []) if p.get("chainId") == "solana"]
    if not pares:
        return None
    p = max(pares, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    return {"simbolo": p["baseToken"].get("symbol"), "nombre": p["baseToken"].get("name"),
            "precio": float(p.get("priceUsd") or 0), "liquidez": (p.get("liquidity") or {}).get("usd") or 0,
            "mcap": p.get("marketCap") or p.get("fdv") or 0, "creado": (p.get("pairCreatedAt") or 0) / 1000,
            "cambio_1h": (p.get("priceChange") or {}).get("h1"), "cambio_24h": (p.get("priceChange") or {}).get("h24"),
            "vol_24h": (p.get("volume") or {}).get("h24"), "url": p.get("url")}


def rugcheck(mint):
    try:
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary", timeout=20)
        j = r.json()
        riesgos = [x.get("name") for x in j.get("risks", []) if x.get("level") == "danger"]
        return {"puntaje": j.get("score_normalised", j.get("score")), "peligros": riesgos}
    except Exception:
        return {"puntaje": None, "peligros": []}


def revisar_seguridad(mint):
    d = dexscreener(mint)
    if not d:
        return None, "sin par de trading en DexScreener"
    rc = rugcheck(mint)
    edad_min = (ahora_ts() - d["creado"]) / 60 if d["creado"] else 9999
    motivos = []
    if d["liquidez"] < FILTROS["liquidez_min"]:
        motivos.append(f"liquidez baja (${d['liquidez']:,.0f})")
    if not (FILTROS["mcap_min"] <= d["mcap"] <= FILTROS["mcap_max"]):
        motivos.append(f"market cap fuera de rango (${d['mcap']:,.0f})")
    if edad_min < FILTROS["edad_min_min"]:
        motivos.append(f"moneda muy nueva ({edad_min:.0f} min)")
    if rc["peligros"]:
        motivos.append("RugCheck: " + ", ".join(rc["peligros"][:3]))
    d.update(rugcheck=rc, edad_min=round(edad_min))
    return d, "; ".join(motivos)


# ───────── auditor ─────────

def auditar(st):
    for s in st.m["senales"]:
        if s.get("estado") != "enviada" or s.get("final"):
            continue
        horas = (ahora_ts() - s["ts_num"]) / 3600
        try:
            d = dexscreener(s["mint"])
        except Exception:
            continue
        if not d or not s.get("precio_senal"):
            continue
        cambio = round((d["precio"] / s["precio_senal"] - 1) * 100, 1)
        s["max_pct"] = max(s.get("max_pct", cambio), cambio)
        for h in (1, 6, 24):
            k = f"pct_{h}h"
            if horas >= h and k not in s:
                s[k] = cambio
        if horas >= 24:
            s["final"] = True
            s["resultado"] = "ganadora" if s.get("pct_24h", 0) > 0 or s.get("max_pct", 0) >= 50 else "perdedora"
    cerradas = [s for s in st.m["senales"] if s.get("final")]
    if cerradas:
        g = sum(1 for s in cerradas if s["resultado"] == "ganadora")
        st.m["estadisticas"] = {"senales": len(cerradas), "ganadoras": g, "acierto_pct": round(g / len(cerradas) * 100),
                                "promedio_24h_pct": round(sum(s.get("pct_24h", 0) for s in cerradas) / len(cerradas), 1),
                                "mejor_max_pct": max(s.get("max_pct", 0) for s in cerradas)}
        # rendimiento por wallet
        por = {}
        for s in cerradas:
            for w in s["wallets"]:
                x = por.setdefault(w, {"senales": 0, "ganadoras": 0})
                x["senales"] += 1
                x["ganadoras"] += s["resultado"] == "ganadora"
        for w, x in por.items():
            st.m["wallets"].get(w, {})["acierto"] = round(x["ganadoras"] / x["senales"] * 100)
            st.m["wallets"].get(w, {})["senales"] = x["senales"]


# ───────── turno ─────────

def correr():
    st = Estado()
    m = st.m
    nombres = {w: d["nombre"] for w, d in m["wallets"].items()}

    st.paso("vigia", f"Revisando las compras de {len(m['wallets'])} wallets top en Solana…")
    nuevos = 0
    for w, d in m["wallets"].items():
        if not d.get("activa", True):
            continue
        try:
            ultima, movs = movimientos(w, d.get("ultima_firma"))
        except Exception as ex:
            print("RPC falló", d["nombre"], ex)
            continue
        primera_vez = not d.get("ultima_firma")
        d["ultima_firma"] = ultima
        if primera_vez:
            continue  # la primera vez solo marca el punto de partida
        for t in movs:
            t["nombre"] = d["nombre"]
            m["trades"].insert(0, t)
            nuevos += 1
        time.sleep(0.4)
    st.paso("vigia", f"{nuevos} movimientos nuevos de las wallets top.", trabajando=False)

    # señales: 2+ wallets distintas compran la misma moneda en la ventana
    st.paso("senal", "Buscando monedas que compraron varias wallets top a la vez…")
    corte = ahora_ts() - VENTANA_H * 3600
    compras = {}
    for t in m["trades"]:
        if t["lado"] == "compra" and t["ts"] >= corte:
            compras.setdefault(t["mint"], {}).setdefault(t["wallet"], t)
    vistos = {s["mint"] for s in m["senales"]}
    candidatos = [(mint, ws) for mint, ws in compras.items() if len(ws) >= MIN_WALLETS and mint not in vistos]
    st.paso("senal", f"{len(candidatos)} monedas con compras de {MIN_WALLETS}+ wallets top.", trabajando=False)

    for mint, ws in candidatos[:5]:
        quienes = [nombres.get(w, w[:4]) for w in ws]
        st.paso("seguridad", f"Revisando seguridad de {mint[:6]}… (compraron {', '.join(quienes)})")
        try:
            d, motivo = revisar_seguridad(mint)
        except Exception as ex:
            d, motivo = None, f"no se pudo revisar ({str(ex)[:50]})"
        senal = {"mint": mint, "wallets": list(ws), "quienes": quienes, "ts": iso(), "ts_num": ahora_ts(),
                 "sol_total": round(sum(t["sol"] for t in ws.values()), 2), "datos": d}
        if motivo:
            senal["estado"] = "descartada"
            senal["motivo"] = motivo
            st.paso("seguridad", f"Descartada {d['simbolo'] if d else mint[:6]}: {motivo}", trabajando=False)
        else:
            senal["estado"] = "enviada"
            senal["precio_senal"] = d["precio"]
            st.paso("seguridad", f"🚨 SEÑAL: {d['simbolo']} — la compraron {', '.join(quienes)}.", trabajando=False)
            base.avisar_telefono(
                f"🚨 Memecoin: {d['simbolo']} ({len(ws)} wallets top)",
                f"Compraron: {', '.join(quienes)} ({senal['sol_total']} SOL en total)\n"
                f"MC ${d['mcap']:,.0f} · Liquidez ${d['liquidez']:,.0f} · Edad {d['edad_min']} min\n"
                f"1h {d['cambio_1h']}% · RugCheck {d['rugcheck']['puntaje']}\n"
                f"Contrato: {mint}\n{d['url']}\n\nTú decides si compras en fomo. Solo dinero que puedas perder.", "high")
        m["senales"].insert(0, senal)

    # salidas: si 2+ de los que compraron una señal ya vendieron
    for s in m["senales"]:
        if s.get("estado") != "enviada" or s.get("aviso_salida"):
            continue
        vendieron = {t["wallet"] for t in m["trades"] if t["mint"] == s["mint"] and t["lado"] == "venta" and t["ts"] >= s["ts_num"] - 600}
        if len(vendieron & set(s["wallets"])) >= 2:
            s["aviso_salida"] = iso()
            simb = (s.get("datos") or {}).get("simbolo", s["mint"][:6])
            st.paso("senal", f"⚠️ SALIDA: las wallets top están vendiendo {simb}.", trabajando=False)
            base.avisar_telefono(f"⚠️ Salida: venden {simb}", f"{len(vendieron)} de las wallets que compraron {simb} ya vendieron. Considera tomar ganancia o salir.", "high")

    st.paso("auditor", "Midiendo cómo van las señales anteriores…")
    try:
        auditar(st)
    except Exception as ex:
        print("auditor:", ex)
    e = m.get("estadisticas")
    st.paso("auditor", f"Acierto {e['acierto_pct']}% en {e['senales']} señales cerradas." if e else "Todavía no hay señales cerradas.", trabajando=False)
    m["ultimo_turno"] = iso()
    st.guardar("turno")


if __name__ == "__main__":
    correr()
