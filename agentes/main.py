"""Agentes de Cliente Directo.

Uso:  python agentes/main.py <buscador|disenador|vendedor|seguimiento|gerente>

Corre gratis en GitHub Actions. Todo el estado vive en data/estado.json
(el panel lo lee en vivo) y los cambios que haces desde el panel llegan
en data/cambios.json.
"""
import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import smtplib
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

RAIZ = Path(__file__).resolve().parent.parent
ESTADO = RAIZ / "data" / "estado.json"
CAMBIOS = RAIZ / "data" / "cambios.json"
MUESTRAS = RAIZ / "muestras"
PLANTILLA = RAIZ / "plantilla" / "muestra.html"
LV = ZoneInfo("America/Los_Angeles")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GMAIL_USER = os.environ.get("GMAIL_USER", "").strip()
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
SITIO = os.environ.get("SITIO_URL", "").rstrip("/")  # https://usuario.github.io/repo
EN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
MODELOS = [m for m in [os.environ.get("GEMINI_MODEL", "").strip(),
                       "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"] if m]


# ───────────────────────── utilidades ─────────────────────────

def ahora():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lv_ahora():
    return datetime.now(LV)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def dias_desde(s):
    t = parse_ts(s)
    if not t:
        return 999
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def slug(texto):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:60] or "negocio"


def solo_digitos(tel):
    d = re.sub(r"\D", "", tel or "")
    if len(d) == 10:
        d = "1" + d
    return d


def cargar():
    return json.loads(ESTADO.read_text(encoding="utf-8"))


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True, check=check)


class Oficina:
    """Guarda el estado y lo publica para que el panel lo vea en vivo."""

    def __init__(self, agente):
        self.agente = agente
        if EN_ACTIONS:
            git("pull", "--rebase", "--quiet", check=False)
        self.e = cargar()
        self.aplicar_cambios_del_panel()

    @property
    def cfg(self):
        return self.e["config"]

    @property
    def pros(self):
        return self.e["prospectos"]

    def aplicar_cambios_del_panel(self):
        try:
            cambios = json.loads(CAMBIOS.read_text(encoding="utf-8"))
        except Exception:
            return
        ultimo = self.e.get("cambios_aplicados", "")
        for c in sorted(cambios, key=lambda c: c.get("ts", "")):
            if c.get("ts", "") <= ultimo:
                continue
            p = self.pros.get(c.get("id"))
            if p is not None:
                for k in ("etapa", "notas", "email"):
                    if k in c:
                        p[k] = c[k]
                if c.get("etapa") == "contactado" and not p.get("ultimo_contacto"):
                    p["ultimo_contacto"] = c["ts"]
                    p["canal"] = p.get("canal") or "sms"
            elif c.get("id") == "config" and isinstance(c.get("config"), dict):
                permitidos = {"direccion_postal", "modo_envio", "cuota_por_turno", "max_correos_dia"}
                for k, v in c["config"].items():
                    if k in permitidos:
                        self.cfg[k] = v
            self.e["cambios_aplicados"] = c["ts"]

    def guardar(self, mensaje="actualiza estado"):
        self.e["actividad"] = self.e["actividad"][:300]
        ESTADO.write_text(json.dumps(self.e, ensure_ascii=False, indent=1), encoding="utf-8")
        if not EN_ACTIONS:
            return
        git("add", "-A")
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        git("commit", "-q", "-m", f"[{self.agente}] {mensaje}")
        for intento in range(4):
            if git("push", "-q", check=False).returncode == 0:
                return
            git("pull", "--rebase", "-X", "theirs", "--quiet", check=False)
            time.sleep(2 + intento * 2)
        print("No se pudo publicar el estado", file=sys.stderr)

    def tarea(self, texto):
        a = self.e["agentes"].setdefault(self.agente, {})
        a.update(estado="trabajando", tarea=texto, actualizado=ahora())
        self.guardar(texto)

    def paso(self, texto, tarea=None):
        print(f"[{self.agente}] {texto}")
        self.e["actividad"].insert(0, {"agente": self.agente, "texto": texto, "ts": ahora()})
        a = self.e["agentes"].setdefault(self.agente, {})
        a.update(estado="trabajando", tarea=tarea or texto, actualizado=ahora())
        self.guardar(texto[:60])

    def fin(self, resumen):
        self.e["actividad"].insert(0, {"agente": self.agente, "texto": "Terminó su turno: " + resumen, "ts": ahora()})
        self.e["agentes"][self.agente] = {"estado": "descansando", "tarea": "", "ultimo_resultado": resumen,
                                          "actualizado": ahora()}
        self.guardar("fin de turno")


def avisar_telefono(titulo, texto, prioridad="default"):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=texto.encode("utf-8"), timeout=15,
                      headers={"Title": titulo.encode("utf-8"), "Priority": prioridad,
                               **({"Click": SITIO} if SITIO else {})})
    except Exception as ex:
        print("ntfy falló:", ex, file=sys.stderr)


# ───────────────────────── Gemini (gratis) ─────────────────────────

_MODELOS_OK = None


def modelos_disponibles():
    """Pregunta a Google qué modelos 'flash' puede usar esta clave (gratis)."""
    global _MODELOS_OK
    if _MODELOS_OK is not None:
        return _MODELOS_OK
    preferidos = [m for m in MODELOS]
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": GEMINI_KEY, "pageSize": 200}, timeout=30)
        nombres = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", [])]
        flash = [n for n in nombres if "flash" in n and "image" not in n and "tts" not in n and "audio" not in n
                 and "live" not in n and "thinking" not in n]
        flash.sort(key=lambda n: (("latest" not in n), ("lite" in n), ("preview" in n or "exp" in n), n), reverse=False)
        print("Modelos disponibles:", flash[:12])
        _MODELOS_OK = [m for m in preferidos if m in nombres] + [m for m in flash if m not in preferidos]
    except Exception as ex:
        print("No pude listar modelos:", ex)
        _MODELOS_OK = preferidos
    return _MODELOS_OK or preferidos


def gemini(prompt, buscar=False, intentos=3):
    if not GEMINI_KEY:
        raise RuntimeError("Falta GEMINI_API_KEY")
    errores = []
    for usar_busqueda in ([True] if buscar else [False]):
        cuerpo = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.6}}
        if usar_busqueda:
            cuerpo["tools"] = [{"google_search": {}}]
        for modelo in modelos_disponibles()[:6]:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
            for i in range(intentos):
                r = requests.post(url, params={"key": GEMINI_KEY}, json=cuerpo, timeout=120)
                if r.status_code == 200:
                    partes = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    texto = "".join(p.get("text", "") for p in partes)
                    if texto.strip():
                        if buscar and not usar_busqueda:
                            print("Aviso: respondió sin búsqueda en Google")
                        return texto
                    errores.append(f"{modelo}: vacío")
                    break
                msg = r.text[:160].replace("\n", " ")
                errores.append(f"{modelo}{'+busqueda' if usar_busqueda else ''}: {r.status_code}")
                print("Gemini", modelo, r.status_code, msg)
                if r.status_code == 429 and i < intentos - 1:
                    time.sleep(15 * (i + 1))
                    continue
                break
        if buscar and usar_busqueda is True:
            print("La búsqueda en Google no funcionó; pruebo sin ella.")
    raise RuntimeError("Gemini no respondió: " + "; ".join(errores[-4:]))


def gemini_json(prompt, buscar=False):
    texto = gemini(prompt + "\n\nResponde SOLO con JSON válido, sin texto antes ni después.", buscar=buscar)
    texto = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.M).strip()
    m = re.search(r"(\[.*\]|\{.*\})", texto, re.S)
    if not m:
        raise ValueError("Gemini no devolvió JSON: " + texto[:200])
    return json.loads(m.group(1))



OSM_FILTROS = {
    "restaurantes": ['nwr["amenity"~"^(restaurant|fast_food|cafe|ice_cream)$"]', 'nwr["shop"~"^(bakery|butcher|deli|confectionery)$"]'],
    "talleres": ['nwr["shop"~"^(car_repair|tyres|motorcycle|car_parts)$"]', 'nwr["amenity"="car_wash"]', 'nwr["craft"="car_repair"]'],
    "herrer": ['nwr["craft"~"^(metal_construction|welder|blacksmith|gardener|carpenter|builder|roofer|plumber|electrician)$"]', 'nwr["shop"~"^(garden_centre|hardware)$"]'],
}


def buscar_osm(nicho, conocidos, cuota):
    """Negocios reales de OpenStreetMap (gratis) sin sitio web, en Las Vegas / North Las Vegas."""
    clave = next((k for k in OSM_FILTROS if k in nicho.lower()), "restaurantes")
    bbox = "(36.08,-115.30,36.32,-114.98)"
    partes = "".join(f'{f}["name"][!"website"][!"contact:website"]{bbox};' for f in OSM_FILTROS[clave])
    q = f"[out:json][timeout:60];({partes});out center tags 300;"
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, timeout=90,
                      headers={"User-Agent": "ClienteDirecto/1.0"})
    r.raise_for_status()
    vistos = {c.lower()[:14] for c in conocidos if c}
    salida = []
    elementos = r.json().get("elements", [])
    import random
    random.shuffle(elementos)
    elementos.sort(key=lambda e: 0 if (e.get("tags", {}).get("phone") or e.get("tags", {}).get("contact:phone")) else 1)
    for el in elementos:
        t = el.get("tags", {})
        nombre = t.get("name", "").strip()
        if not nombre or nombre.lower()[:14] in vistos or t.get("brand") or t.get("brand:wikidata"):
            continue  # sin cadenas grandes
        tel = t.get("phone") or t.get("contact:phone") or ""
        direccion = " ".join(x for x in [t.get("addr:housenumber", ""), t.get("addr:street", "")] if x)
        ciudad = t.get("addr:city", "")
        direccion = ", ".join(x for x in [direccion, ciudad, "NV"] if x) if direccion else ""
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        salida.append({"nombre": nombre, "direccion": direccion, "tel": tel,
                       "email": t.get("email") or t.get("contact:email") or "",
                       "horario": t.get("opening_hours", ""), "rating": "",
                       "redes": t.get("contact:facebook") or t.get("facebook") or t.get("contact:instagram") or "",
                       "productos": t.get("cuisine", "").replace(";", ", ") or t.get("shop", "") or t.get("craft", ""),
                       "tiene_web": False,
                       "por_que": "No tiene página web registrada en el mapa. Verifica en Google antes de ofrecer.",
                       "fuente": f"https://www.openstreetmap.org/{el['type']}/{el['id']}" + (f" · https://www.google.com/maps?q={lat},{lon}" if lat else "")})
        vistos.add(nombre.lower()[:14])
        if len(salida) >= cuota:
            break
    return salida


# ───────────────────────── Gmail (gratis) ─────────────────────────

def gmail_listo():
    return bool(GMAIL_USER and GMAIL_PASS)


def pie(cfg):
    lineas = ["—", f"{cfg['dueno']} · {cfg['empresa']}", f"WhatsApp {cfg['whatsapp']}"]
    if cfg.get("direccion_postal"):
        lineas.append(cfg["direccion_postal"])
    lineas.append("Si no desea recibir más correos, responda BAJA y no le escribiremos más.")
    return "\n".join(lineas)


def armar_correo(cfg, para, asunto, cuerpo, responder_a=None):
    msg = MIMEText(cuerpo.strip() + "\n\n" + pie(cfg), "plain", "utf-8")
    msg["From"] = email.utils.formataddr((f"{cfg['dueno']} · {cfg['empresa']}", GMAIL_USER))
    msg["To"] = para
    msg["Subject"] = asunto
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="clientedirecto.local")
    if responder_a:
        msg["In-Reply-To"] = responder_a
        msg["References"] = responder_a
    return msg


def enviar_o_borrador(cfg, msg):
    """Devuelve 'enviado' o 'borrador'."""
    puede_enviar = cfg.get("modo_envio") == "enviar" and cfg.get("direccion_postal")
    if puede_enviar:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.send_message(msg)
        return "enviado"
    with imaplib.IMAP4_SSL("imap.gmail.com") as im:
        im.login(GMAIL_USER, GMAIL_PASS)
        im.append('"[Gmail]/Drafts"', "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    return "borrador"


def respuestas_de(direccion, desde_dias=21):
    """Lista de (asunto, texto) de correos recibidos de esa dirección."""
    salida = []
    desde = (datetime.now() - timedelta(days=desde_dias)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL("imap.gmail.com") as im:
        im.login(GMAIL_USER, GMAIL_PASS)
        im.select("INBOX", readonly=True)
        _, ids = im.search(None, f'(FROM "{direccion}" SINCE {desde})')
        for i in ids[0].split()[-5:]:
            _, datos = im.fetch(i, "(RFC822)")
            m = email.message_from_bytes(datos[0][1])
            texto = ""
            for parte in m.walk():
                if parte.get_content_type() == "text/plain":
                    texto = parte.get_payload(decode=True).decode(parte.get_content_charset() or "utf-8", "ignore")
                    break
            salida.append((str(email.header.make_header(email.header.decode_header(m.get("Subject", "")))),
                           texto[:1500], m.get("Message-ID")))
    return salida


def en_horario(cfg):
    h = lv_ahora().hour
    return cfg.get("hora_inicio_envio", 8) <= h < cfg.get("hora_fin_envio", 18)


# ───────────────────────── agentes ─────────────────────────

def buscador():
    of = Oficina("buscador")
    cfg = of.cfg
    of.tarea("Revisando la lista para no repetir negocios")
    conocidos = [p.get("nombre", "") for p in of.pros.values()] + cfg.get("excluir", [])
    turno = int(lv_ahora().hour // 2)
    nicho = cfg["nichos"][turno % len(cfg["nichos"])]
    zona = cfg["zonas"][(turno + lv_ahora().day) % len(cfg["zonas"])]
    cuota = int(cfg.get("cuota_por_turno", 3))
    of.paso(f"Buscando {nicho} sin página web cerca de {zona}…")
    prompt = f"""Busca en Google (Maps, Yelp, Facebook, directorios) {cuota + 2} negocios reales de "{nicho}"
en Las Vegas o North Las Vegas, Nevada, cerca de {zona}, que NO tengan página web propia
(si su único sitio es Facebook, Instagram, Yelp o una ficha de directorio, cuenta como sin web).
No incluyas estos negocios (ya los tenemos): {", ".join(conocidos)[:3000]}.
Prefiere negocios hispanos y familiares, y los que tengan email público.
Para cada uno devuelve un objeto con: nombre, direccion, tel, email ("" si no hay), horario,
rating (texto con estrellas y número de reseñas), redes (enlaces), productos (2-4 cosas que venden o
servicios, con precio si aparece), tiene_web (true/false), por_que (1 frase de por qué le conviene una
página con botón de WhatsApp), fuente (enlaces donde lo viste).
No inventes datos: si no lo sabes, deja "". Devuelve una lista JSON."""
    try:
        encontrados = gemini_json(prompt, buscar=True)
    except Exception as ex:
        print("Búsqueda con Gemini no disponible:", ex)
        of.paso("La búsqueda de Google no está disponible gratis; busco en el mapa abierto (OpenStreetMap).")
        try:
            encontrados = buscar_osm(nicho, conocidos, cuota)
        except Exception as ex2:
            of.fin(f"No pude buscar en este turno ({str(ex2)[:80]})")
            return
    agregados = 0
    for n in encontrados if isinstance(encontrados, list) else []:
        if agregados >= cuota:
            break
        nombre = (n.get("nombre") or "").strip()
        if not nombre or n.get("tiene_web") is True:
            if nombre:
                of.paso(f"Descarté {nombre}: ya tiene página web.")
            continue
        if any(nombre.lower()[:12] in c.lower() for c in conocidos if c):
            continue
        clave = slug(nombre + "-" + ("nlv" if "north" in (n.get("direccion") or "").lower() else "lv"))
        if clave in of.pros:
            continue
        of.pros[clave] = {
            "nombre": nombre, "nicho": nicho, "direccion": n.get("direccion", ""), "tel": n.get("tel", ""),
            "email": (n.get("email") or "").strip(), "horario": n.get("horario", ""), "rating": n.get("rating", ""),
            "redes": n.get("redes", "") if isinstance(n.get("redes"), str) else ", ".join(n.get("redes") or []),
            "productos": n.get("productos", "") if isinstance(n.get("productos"), str) else "; ".join(map(str, n.get("productos") or [])),
            "notas": n.get("por_que", ""),
            "fuente": n.get("fuente", "") if isinstance(n.get("fuente"), str) else " ; ".join(n.get("fuente") or []),
            "etapa": "nuevo", "muestra_url": "", "mensaje_texto": "", "creado": ahora(),
            "ultimo_contacto": "", "seguimientos": 0,
        }
        conocidos.append(nombre)
        agregados += 1
        extra = " (tiene email)" if of.pros[clave]["email"] else ""
        of.paso(f"Encontré {nombre}, sin página web{extra}.", tarea=f"Agregando {nombre}")
    of.fin(f"{agregados} negocios nuevos de {nicho.split(' (')[0]} cerca de {zona}")


TIPO_POR_NICHO = {"restaurantes": "comida", "talleres": "taller", "herrer": "oficio"}


def tipo_de(nicho):
    for k, v in TIPO_POR_NICHO.items():
        if k in (nicho or "").lower():
            return v
    return "oficio"


def disenador():
    of = Oficina("disenador")
    cfg = of.cfg
    of.tarea("Revisando qué negocios necesitan muestra")
    pendientes = [(k, p) for k, p in of.pros.items() if p.get("etapa") == "nuevo"][:6]
    if not pendientes:
        of.fin("No había negocios nuevos para armar muestra")
        return
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    hechas = 0
    for clave, p in pendientes:
        of.paso(f"Armando la muestra de {p['nombre']}…", tarea=f"Diseñando {p['nombre']}")
        tipo = tipo_de(p.get("nicho"))
        prompt = f"""Eres diseñador web de "{cfg['empresa']}". Prepara el contenido de una página de muestra para este negocio
y los mensajes para ofrecérsela. Datos: {json.dumps(p, ensure_ascii=False)}
Tipo de página: {tipo} (comida = carta con pedido por WhatsApp; taller = servicios y cotización;
oficio = servicios y cotización con fotos/medidas).
Devuelve un objeto JSON con:
- eslogan: frase corta y cálida (máx. 8 palabras) en español
- color: un color hex que vaya con el negocio (no gris)
- idioma_extra: frase corta en inglés para clientes que no hablan español
- items: 6 a 10 objetos {{nombre, precio, desc}} con lo que vende o los servicios. Usa los productos reales que
  aparecen en los datos; si no hay precios reales pon precios de ejemplo realistas para Las Vegas (en taller u oficio
  puede ser "Cotización gratis"). desc máx. 10 palabras.
- precios_reales: true si los precios vienen de los datos, false si son de ejemplo
- mensaje_texto: SMS de {cfg['dueno']} al dueño (máx. 420 caracteres, español, amable, menciona algo concreto del
  negocio, dice que le armó una muestra gratis, incluye el texto LINK donde va el enlace, cierra con su WhatsApp
  {cfg['whatsapp']}). Sin presión ni promesas falsas.
- email_asunto: asunto honesto (máx. 60 caracteres)
- email_cuerpo: correo de 90-140 palabras, mismo tono, con LINK donde va el enlace, firmado por {cfg['dueno']}."""
        try:
            c = gemini_json(prompt)
        except Exception as ex:
            p["notas"] = (p.get("notas", "") + f" | Diseñador: falló ({str(ex)[:60]})").strip(" |")
            of.paso(f"No pude armar la muestra de {p['nombre']}; lo intento el próximo turno.")
            continue
        datos = {
            "nombre": p["nombre"], "tipo": tipo, "tel": p.get("tel", ""), "wa": solo_digitos(p.get("tel")),
            "direccion": p.get("direccion", ""), "horario": p.get("horario", ""), "rating": p.get("rating", ""),
            "eslogan": c.get("eslogan", ""), "color": c.get("color", "#0f7a52"), "ingles": c.get("idioma_extra", ""),
            "items": c.get("items", [])[:12], "precios_reales": bool(c.get("precios_reales")),
            "empresa": cfg["empresa"], "dueno": cfg["dueno"], "dueno_wa": solo_digitos(cfg["whatsapp"]),
        }
        html = plantilla.replace("/*__DATOS__*/{}", json.dumps(datos, ensure_ascii=False).replace("</", "<\\/"))
        html = html.replace("__TITULO__", f"{p['nombre']} — muestra")
        MUESTRAS.mkdir(exist_ok=True)
        (MUESTRAS / f"{clave}.html").write_text(html, encoding="utf-8")
        enlace = f"{SITIO}/muestras/{clave}.html" if SITIO else f"muestras/{clave}.html"
        p.update(muestra_url=enlace, etapa="muestra", actualizado=ahora(),
                 mensaje_texto=c.get("mensaje_texto", "").replace("LINK", enlace),
                 email_asunto=c.get("email_asunto", ""), email_cuerpo=c.get("email_cuerpo", "").replace("LINK", enlace))
        hechas += 1
        of.paso(f"Muestra lista de {p['nombre']}" + (" y su mensaje para mandar por texto." if not p.get("email") else " y su correo."))
    of.fin(f"{hechas} muestras nuevas")


def vendedor():
    of = Oficina("vendedor")
    cfg = of.cfg
    of.tarea("Revisando a quién escribirle")
    listos = [(k, p) for k, p in of.pros.items()
              if p.get("etapa") == "muestra" and p.get("email") and p.get("muestra_url")
              and p["email"].lower() not in of.e["bajas"]]
    sin_email = sum(1 for p in of.pros.values() if p.get("etapa") == "muestra" and not p.get("email"))
    if not gmail_listo():
        of.fin(f"Gmail no está conectado. {len(listos)} correos esperando; {sin_email} mensajes de texto listos para ti")
        return
    if not en_horario(cfg):
        of.fin(f"Fuera de horario: {len(listos)} correos listos para la mañana")
        return
    hoy = lv_ahora().date()
    enviados_hoy = sum(1 for p in of.pros.values() if p.get("canal") == "email" and parse_ts(p.get("ultimo_contacto"))
                       and parse_ts(p["ultimo_contacto"]).astimezone(LV).date() == hoy)
    cupo = max(0, int(cfg.get("max_correos_dia", 15)) - enviados_hoy)
    hechos = {"enviado": 0, "borrador": 0}
    for clave, p in listos[:cupo]:
        of.paso(f"Escribiéndole a {p['nombre']}…", tarea=f"Correo para {p['nombre']}")
        try:
            msg = armar_correo(cfg, p["email"], p.get("email_asunto") or f"Una página para {p['nombre']}",
                               p.get("email_cuerpo") or p.get("mensaje_texto", ""))
            r = enviar_o_borrador(cfg, msg)
        except Exception as ex:
            of.paso(f"No pude escribirle a {p['nombre']} ({str(ex)[:60]}).")
            continue
        hechos[r] += 1
        p.update(etapa="contactado", ultimo_contacto=ahora(), canal="email", email_estado=r, seguimientos=0,
                 message_id=msg["Message-ID"])
        of.paso(f"{'Correo enviado' if r == 'enviado' else 'Borrador listo en tu Gmail'} para {p['nombre']}.")
    of.fin(f"{hechos['enviado']} enviados, {hechos['borrador']} en borrador, {sin_email} textos listos para ti")


def seguimiento():
    of = Oficina("seguimiento")
    cfg = of.cfg
    of.tarea("Revisando respuestas en Gmail")
    nuevas = bajas = segs = 0
    activos = [(k, p) for k, p in of.pros.items() if p.get("etapa") in ("contactado", "seguimiento")]
    if gmail_listo():
        for clave, p in activos:
            if not p.get("email"):
                continue
            try:
                resp = respuestas_de(p["email"])
            except Exception as ex:
                of.paso(f"No pude revisar Gmail ({str(ex)[:60]}).")
                break
            if not resp:
                continue
            texto = "\n---\n".join(f"Asunto: {a}\n{t}" for a, t, _ in resp)
            try:
                c = gemini_json(f"""Un negocio respondió a nuestra oferta de página web. Clasifica su respuesta.
Respuesta(s): {texto[:3000]}
Devuelve {{"tipo": "interes" | "baja" | "otro", "resumen": "1 línea en español de lo que dijo",
"sugerencia": "1 línea de qué contestarle para cerrar la venta"}}""")
            except Exception:
                c = {"tipo": "otro", "resumen": resp[0][1][:120], "sugerencia": "Léelo y contéstale tú."}
            if c.get("tipo") == "baja":
                p["etapa"] = "descartado"
                of.e["bajas"][p["email"].lower()] = {"fecha": ahora(), "motivo": c.get("resumen", "")}
                bajas += 1
                of.paso(f"{p['nombre']} pidió no recibir más correos. Lo quité de la lista.")
            else:
                p["etapa"] = "respondio"
                p["notas"] = f"Dijo: {c.get('resumen', '')} → Contéstale: {c.get('sugerencia', '')}"
                nuevas += 1
                of.paso(f"🔔 RESPONDIÓ: {p['nombre']} — {c.get('resumen', '')}")
                avisar_telefono(f"🔔 {p['nombre']} respondió", f"{c.get('resumen', '')}\nTel: {p.get('tel', '')}\n"
                                f"Sugerencia: {c.get('sugerencia', '')}", "high")
        if en_horario(cfg):
            for clave, p in activos:
                if p.get("etapa") not in ("contactado", "seguimiento") or not p.get("email") or p.get("canal") != "email":
                    continue
                if p.get("email_estado") == "borrador":
                    continue  # Gent aún no lo ha enviado
                n, d = int(p.get("seguimientos", 0)), dias_desde(p.get("ultimo_contacto"))
                if n >= 2 and d >= 7:
                    p["etapa"] = "descartado"
                    p["notas"] = (p.get("notas", "") + " | sin respuesta").strip(" |")
                    of.paso(f"{p['nombre']} no respondió en 3 intentos; lo marqué como descartado.")
                    continue
                if (n == 0 and d >= 3) or (n == 1 and d >= 4):
                    cuerpo = (f"Hola, le escribo de nuevo por la página de muestra que le armé a {p['nombre']}: "
                              f"{p['muestra_url']}\nSi quiere le cambio fotos, precios o colores sin costo para que la vea "
                              f"a su gusto. Me puede contestar aquí o por WhatsApp al {cfg['whatsapp']}."
                              if n == 0 else
                              f"Último mensaje de mi parte. Si no es buen momento, no hay problema. La muestra de "
                              f"{p['nombre']} sigue aquí cuando la quiera ver: {p['muestra_url']}")
                    try:
                        msg = armar_correo(cfg, p["email"], "Re: " + (p.get("email_asunto") or p["nombre"]), cuerpo,
                                           p.get("message_id"))
                        r = enviar_o_borrador(cfg, msg)
                    except Exception as ex:
                        of.paso(f"No pude darle seguimiento a {p['nombre']} ({str(ex)[:60]}).")
                        continue
                    p.update(etapa="seguimiento", seguimientos=n + 1, ultimo_contacto=ahora(), email_estado=r)
                    segs += 1
                    of.paso(f"Seguimiento {n + 1} {'enviado' if r == 'enviado' else 'en borrador'} a {p['nombre']}.")
    # contactados por texto: recordar seguimiento
    for clave, p in of.pros.items():
        if p.get("etapa") == "contactado" and p.get("canal") == "sms" and dias_desde(p.get("ultimo_contacto")) >= 3 \
                and "Toca seguimiento por texto" not in p.get("notas", ""):
            p["notas"] = (p.get("notas", "") + " | Toca seguimiento por texto").strip(" |")
            p["mensaje_texto"] = (f"Hola, soy {cfg['dueno']} de nuevo. ¿Pudo ver la página de muestra de {p['nombre']}? "
                                  f"{p.get('muestra_url', '')} Si quiere le cambio lo que guste, sin compromiso.")
            of.paso(f"Toca mandarle otro texto a {p['nombre']}; el mensaje ya está listo en el panel.")
    if not gmail_listo():
        of.fin("Gmail no está conectado; solo revisé los contactos por texto")
    else:
        of.fin(f"{nuevas} respuestas nuevas, {bajas} bajas, {segs} seguimientos")


def gerente():
    of = Oficina("gerente")
    cfg = of.cfg
    of.tarea("Revisando cómo va el equipo")
    for nombre, a in of.e["agentes"].items():
        if a.get("estado") == "trabajando" and dias_desde(a.get("actualizado")) * 24 * 60 > 90:
            a.update(estado="descansando", tarea="", ultimo_resultado="turno interrumpido", actualizado=ahora())
            of.paso(f"El turno de {nombre} se cortó; lo puse a descansar.")
    pros = of.pros
    hoy = lv_ahora().date()

    def de_hoy(p, campo):
        t = parse_ts(p.get(campo))
        return bool(t) and t.astimezone(LV).date() == hoy

    por_etapa = {}
    for p in pros.values():
        por_etapa[p.get("etapa", "nuevo")] = por_etapa.get(p.get("etapa", "nuevo"), 0) + 1
    respondieron = [p for p in pros.values() if p.get("etapa") == "respondio"]
    textos = [p for p in pros.values() if p.get("etapa") == "muestra" and not p.get("email")]
    textos += [p for p in pros.values() if "Toca seguimiento por texto" in p.get("notas", "") and p.get("etapa") == "contactado"]
    borradores = [p for p in pros.values() if p.get("email_estado") == "borrador" and p.get("etapa") in ("contactado", "seguimiento")]
    of.paso("Contando cómo vamos hoy…")
    lineas = []
    if respondieron:
        lineas.append("LLAMA HOY PARA CERRAR:")
        lineas += [f"• {p['nombre']} {p.get('tel', '')} — {p.get('notas', '')[:120]}" for p in respondieron]
    if textos:
        lineas.append(f"MANDA ESTOS TEXTOS ({len(textos)}): " + ", ".join(p["nombre"] for p in textos[:8])
                      + (" y más" if len(textos) > 8 else "") + ". Cópialos del panel y márcalos como Contactado.")
    if borradores:
        lineas.append(f"BORRADORES EN GMAIL: {len(borradores)}. Revísalos y envíalos"
                      + ("" if cfg.get("direccion_postal") else ", o pon tu dirección postal en el panel para que salgan solos") + ".")
    nuevos_hoy = sum(1 for p in pros.values() if de_hoy(p, "creado"))
    lineas.append(f"NÚMEROS: {len(pros)} prospectos · {nuevos_hoy} nuevos hoy · "
                  + " · ".join(f"{v} {k}" for k, v in sorted(por_etapa.items())))
    titulo = (f"{len(respondieron)} respondieron · llama a {respondieron[0]['nombre']}" if respondieron
              else f"{len(textos)} textos listos para mandar" if textos else "El equipo sigue prospectando")
    of.e["reportes"][hoy.isoformat()] = {"fecha": ahora(), "titulo": titulo, "texto": "\n".join(lineas)}
    of.paso("Actualicé el reporte del día.")
    h = lv_ahora().hour
    if h in (7, 8, 17, 18) and not of.e.get("aviso_" + hoy.isoformat() + ("am" if h < 12 else "pm")):
        of.e["aviso_" + hoy.isoformat() + ("am" if h < 12 else "pm")] = True
        avisar_telefono(titulo, "\n".join(lineas)[:900])
    of.fin(titulo)


AGENTES = {"buscador": buscador, "disenador": disenador, "vendedor": vendedor,
           "seguimiento": seguimiento, "gerente": gerente}

if __name__ == "__main__":
    nombre = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if nombre not in AGENTES:
        sys.exit(f"Uso: python agentes/main.py <{'|'.join(AGENTES)}>")
    AGENTES[nombre]()
