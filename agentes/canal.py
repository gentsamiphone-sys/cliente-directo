"""Canal de señales GSAM en Telegram: publica los setups que detectan los agentes (cripto, NQ y oro).
Solo LEE data/cripto.json y data/mesa.json; no opera nada. Estado en data/canal.json (qué ya se publicó).
Secrets: TELEGRAM_TOKEN (de @BotFather) y TELEGRAM_CANAL (@nombre del canal o id -100…)."""
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
ESTADO = RAIZ / "data" / "canal.json"
PIE = "\n\n<i>GSAM Capital · señal educativa, no es consejo financiero. Opera bajo tu propio riesgo.</i>"
SALAS = ("https://gentsamiphone-sys.github.io/cliente-directo/sala3d.html (cripto) · "
         "https://gentsamiphone-sys.github.io/cliente-directo/salanq.html (NQ)")


def _leer(nombre):
    try:
        return json.loads((RAIZ / "data" / nombre).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _limpiar(t):
    """Quita montos de la cuenta y etiquetas internas: el cliente ve dirección, entrada, stop, objetivo y R:R."""
    t = re.sub(r"\[(SIMULADO|DEMO)\]\s*", "", t)
    t = re.sub(r"compra \$[\d.,]+ (si el precio entra en|en) ", "entrada ", t)
    t = re.sub(r"\s*·\s*riesgo ~\$[^·]*", "", t)
    t = re.sub(r"COMPRADO \$[\d.,]+ a ~", "dentro a ~", t)
    t = re.sub(r":\s*[+-][\d.,]+ USD\.?", "", t)
    t = re.sub(r"(\d[\d,]*)–\1", r"\1", t)          # "80,350–80,350" → "80,350"
    t = re.sub(r"\b\d+ (MBT|MET)\b", lambda m: {"MBT": "BTC", "MET": "ETH"}[m.group(1)], t)
    return re.sub(r"\s*·\s*", " · ", t).strip()


def _cripto(desde):
    out = []
    claves = ("Idea de COMPRA", "Desplazamiento de COMPRA", "Vela de desplazamiento", "setup de desplazamiento",
              "dentro", "COMPRADO", "cerrada en", "llegó al TP1", "Brief publicado")
    for a in reversed(_leer("cripto.json").get("actividad", [])):
        if a.get("ts", "") <= desde or not any(k in a.get("texto", "") for k in claves):
            continue
        t = _limpiar(a["texto"])
        if "Brief publicado" in t:
            out.append((a["ts"], f"🧠 <b>Mesa Cripto · brief</b>\n{html.escape(t.replace('Brief publicado: ', ''))}"))
        elif "cerrada en" in t or "TP1" in t:
            out.append((a["ts"], f"📌 <b>Cripto · actualización</b>\n{html.escape(t)}"))
        else:
            ico = "🔴" if "VENTA" in t else "🟢"
            out.append((a["ts"], f"{ico} <b>Señal Cripto</b>\n{html.escape(t)}"))
    return out


def _estrategia_4h(publicados):
    out = []
    e = _leer("mesa.json").get("estrategia_4h") or {}
    nombres = {"nq": "NQ (Nasdaq)", "oro": "ORO"}
    for act, d in e.items():
        if not isinstance(d, dict):
            continue
        for s in d.get("setups", []):
            clave = f"4h|{act}|{s.get('direccion')}|{s.get('nivel')}|{s.get('estado')}"
            if clave in publicados:
                continue
            publicados.append(clave)
            ico = "🔴" if s.get("direccion") == "VENTA" else "🟢"
            txt = (f"{ico} <b>{nombres.get(act, act.upper())} · Ruptura y retesteo 4H</b>\n"
                   f"{s.get('direccion')} · estado: {html.escape(str(s.get('estado')))}\n"
                   f"Entrada {s.get('entrada')} · Stop {s.get('stop')} · Objetivo {s.get('objetivo')} · R:R {s.get('rr')}")
            out.append((e.get("ts", ""), txt))
    return out


def _mesa_ordenes(desde):
    out = []
    for a in reversed(_leer("mesa.json").get("actividad", [])):
        t = a.get("texto", "")
        if a.get("ts", "") <= desde or a.get("agente") != "ejecucion" or not re.search(r"\b(COMPRA|VENTA)\b", t):
            continue
        if any(k in t for k in ("No se", "no se", "Error", "error")):
            continue
        out.append((a["ts"], f"🎯 <b>Mesa NQ/Oro · orden</b>\n{html.escape(_limpiar(t))}"))
    return out


def enviar(texto):
    tok, canal = os.environ.get("TELEGRAM_TOKEN", "").strip(), os.environ.get("TELEGRAM_CANAL", "").strip()
    if not (tok and canal):
        print("[sin Telegram]", texto)
        return True
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": canal, "text": texto + PIE, "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=20)
    if r.status_code >= 300:
        print("Telegram:", r.status_code, r.text[:200])
    return r.status_code < 300


def main(modo="publicar"):
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    st = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else None
    if st is None or modo == "reiniciar":
        # primera vez: no se publica lo viejo, se arranca desde ahora
        st = {"desde": ahora, "publicados": []}
        _estrategia_4h(st["publicados"])
        if modo != "prueba":
            ESTADO.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            enviar(f"✅ <b>Canal GSAM activo</b>\nAquí llegan los setups de la mesa cripto (BTC/ETH) y de NQ y oro en cuanto los agentes los detecten.\n🏢 Salas en vivo: {SALAS}")
            return
    if modo == "prueba":
        return enviar("🧪 <b>Prueba del canal GSAM</b>\nSi ves esto, el bot de Telegram está bien conectado.")
    msgs = sorted(_cripto(st["desde"]) + _mesa_ordenes(st["desde"]) + _estrategia_4h(st["publicados"]), key=lambda x: x[0])
    for _, m in msgs[:10]:
        enviar(m)
    st["desde"] = max([st["desde"]] + [a.get("ts", "") for f in ("cripto.json", "mesa.json") for a in _leer(f).get("actividad", [])[:1]])
    st["publicados"] = st["publicados"][-200:]
    ESTADO.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(msgs)} mensajes publicados")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "publicar")
