"""Vigilante EXTERNO de todos los productos de Julián (HeyDaddy, Originl, FirstStone, Settr, Cimiento, Marca Personal
y Emprendedores Paraguay). Comprueba cada 5 minutos, desde fuera de Railway y Vercel, que cada web y API responden
y, si algo cae o vuelve, avisa a Julián por correo (y por Telegram si está configurado) con el nombre del producto
delante.

Corre en GitHub Actions (repositorio público margisjul/heydaddy-vigia, minutos gratis), así que sigue avisando
aunque un proveedor entero se caiga. Solo librería estándar. El estado (fallos seguidos, caído desde cuándo, último
aviso) viaja entre ejecuciones en un JSON.

PARA AÑADIR UN PRODUCTO: una entrada nueva en ``PRODUCTOS`` (abajo). Nada más.

Secretos y variables (todos opcionales salvo la clave de Resend):
  RESEND_API_KEY       clave de Resend (solo envío). Sin ella no sale ningún correo.
  ALERT_EMAIL          a quién (por defecto julian@ghgroup.es)
  EMAIL_FROM           remitente (por defecto "Vigilante <avisos@getoriginl.com>")
  TELEGRAM_BOT_TOKEN   opcional: bot de Telegram. Sin él (o sin chat) solo se manda correo.
  TELEGRAM_CHAT_ID     opcional: chat de Julián
  ORIGINL_EDITOR_URL   opcional: dirección pública del editor de Originl. Sin ella, esa comprobación se omite.

Uso:
  python vigia.py <fichero_estado.json>     pasada real: comprueba, avisa si toca y guarda el estado
  python vigia.py --sin-avisos              solo comprueba y enseña la tabla; no avisa ni guarda nada
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional

# ---------------------------------------------------------------------------------------------------------------
# LISTA DE PRODUCTOS. Cada comprobación: id (estable: es la clave del estado), nombre visible (con artículo, para
# frases como «<nombre> no responde»), url, texto que debe aparecer en la respuesta, qué afecta y qué hacer.
# ``url_env``: si la dirección no es pública, se lee de esa variable (más ``ruta``); si falta, se omite sin fallar.
# ---------------------------------------------------------------------------------------------------------------
PRODUCTOS = [
    {"nombre": "HeyDaddy", "checks": [
        {"id": "web", "nombre": "la web (www.heydaddy.app)", "url": "https://www.heydaddy.app/login",
         "aguja": "heydaddy",
         "afecta": "Los clientes no pueden entrar en su panel.",
         "hacer": "Mira el servicio web de HeyDaddy en Railway."},
        {"id": "api", "nombre": "la API (servidor)", "url": "https://api.heydaddy.app/health",
         "aguja": '"status":"ok"',
         "afecta": "Los chats con fans, los cobros y el panel pueden dejar de funcionar.",
         "hacer": "Mira el servicio api de HeyDaddy en Railway."},
        {"id": "api_web", "nombre": "la API a través de la web", "url": "https://www.heydaddy.app/api/health",
         "aguja": '"status":"ok"',
         "afecta": "El panel no puede hablar con el servidor.",
         "hacer": "Mira la web y la API de HeyDaddy en Railway."},
    ]},
    {"nombre": "Originl", "checks": [
        {"id": "originl_web", "nombre": "la web de Originl (www.getoriginl.com)", "url": "https://www.getoriginl.com/",
         "aguja": "originl",
         "afecta": "Los clientes no pueden entrar en la web ni en su panel.",
         "hacer": "Mira el servicio web de Originl en Railway."},
        {"id": "originl_api", "nombre": "la API de Originl (api.getoriginl.com)",
         "url": "https://api.getoriginl.com/health", "aguja": '"status":"ok"',
         "afecta": "El panel no carga datos, no se cobra ni se programa nada.",
         "hacer": "Mira el servicio api de Originl en Railway."},
        # La salud del editor en Railway es la página «/entrar» del motor, así que basta con que responda.
        {"id": "originl_editor", "nombre": "el editor de vídeo de Originl", "url_env": "ORIGINL_EDITOR_URL",
         "ruta": "/entrar", "aguja": "",
         "afecta": "No se crean variantes nuevas ni se publica lo programado.",
         "hacer": "Mira el servicio del motor de Originl en Railway."},
    ]},
    {"nombre": "FirstStone", "checks": [
        {"id": "firststone_web", "nombre": "la web (firststone.vc)", "url": "https://firststone.vc/",
         "aguja": "firststone",
         "afecta": "Nadie puede entrar en la plataforma ni ver la web.",
         "hacer": "Mira el despliegue de FirstStone en Vercel (rama deploy-preview)."},
        # No hay ruta /health; esta ruta pública lee la base de datos, así que también prueba Supabase.
        {"id": "firststone_datos", "nombre": "el acceso a datos (firststone.vc)",
         "url": "https://firststone.vc/api/launch/status", "aguja": '"active"',
         "afecta": "La plataforma carga, pero puede que no lea la base de datos (ofertas, panel).",
         "hacer": "Mira Supabase y los registros de errores en Vercel."},
    ]},
    {"nombre": "Settr", "checks": [
        {"id": "settr_web", "nombre": "la web (trysettr.com)", "url": "https://trysettr.com/", "aguja": "settr",
         "afecta": "Los clientes de Settr no pueden entrar y los lead magnets pueden dejar de captar.",
         "hacer": "Settr se sirve desde el mismo despliegue de FirstStone en Vercel: mira ese despliegue."},
        {"id": "settr_datos", "nombre": "el acceso a datos (trysettr.com)",
         "url": "https://trysettr.com/api/launch/status", "aguja": '"active"',
         "afecta": "Settr carga, pero puede que no lea la base de datos.",
         "hacer": "Mira Supabase y los registros de errores en Vercel."},
    ]},
    {"nombre": "Cimiento", "checks": [
        {"id": "cimiento_web", "nombre": "el panel (cimiento.trysettr.com)",
         "url": "https://cimiento.trysettr.com/entrar", "aguja": "cimiento",
         "afecta": "Los negocios no pueden entrar y el agente de WhatsApp puede dejar de atender.",
         "hacer": "Mira el servicio web y el worker del agente de WhatsApp en Railway."},
    ]},
    {"nombre": "Marca Personal", "checks": [
        {"id": "marca_web", "nombre": "el panel (marca-personal-production.up.railway.app)",
         "url": "https://marca-personal-production.up.railway.app/entrar", "aguja": "entrar",
         "afecta": "Los clientes no pueden entrar en su panel de marca personal.",
         "hacer": "Mira el servicio de Marca Personal en Railway."},
    ]},
    {"nombre": "Emprendedores Paraguay", "checks": [
        {"id": "paraguay_web", "nombre": "la web (emprendedores-paraguay.vercel.app)",
         "url": "https://emprendedores-paraguay.vercel.app/", "aguja": "paraguay",
         "afecta": "La web pública de la comunidad no se ve.",
         "hacer": "Mira el despliegue en Vercel."},
    ]},
]

#: Cuántas pasadas seguidas (cada una con dos intentos separados) tienen que fallar antes del primer aviso.
#: Con el cron de 5 min, 2 pasadas = unos 5-10 min: un reinicio de un minuto no avisa.
FALLOS_PARA_AVISAR = 2
RECORDATORIO_S = 30 * 60  # mientras siga caído, un recordatorio cada 30 min
REMITENTE_POR_DEFECTO = "Vigilante <avisos@getoriginl.com>"
DESTINATARIO_POR_DEFECTO = "julian@ghgroup.es"


def activas(productos: list = PRODUCTOS, entorno: Optional[dict] = None) -> list[dict]:
    """Aplana las comprobaciones, con ``producto`` y ``url`` resueltas. Las que dependen de una variable que falta
    (o que no es https) se omiten sin fallar."""
    env = os.environ if entorno is None else entorno
    out = []
    for p in productos:
        for c in p["checks"]:
            url = c.get("url", "")
            if c.get("url_env"):
                base = (env.get(c["url_env"]) or "").strip().rstrip("/")
                if not base.startswith("https://"):
                    continue
                url = base + c.get("ruta", "")
            out.append({**c, "producto": p["nombre"], "url": url})
    return out


def pedir(url: str, timeout: float = 15.0) -> tuple[Optional[int], str]:
    req = urllib.request.Request(url, headers={"User-Agent": "heydaddy-vigia/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (URLs fijas https)
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__


def comprobar(url: str, aguja: str, pedir_fn: Callable = pedir, espera_s: float = 20.0) -> tuple[bool, str]:
    """Dos intentos separados (un fallo suelto de red no es una caída)."""
    detalle = ""
    aguja = aguja.lower().replace(" ", "")
    for intento in range(2):
        st, cuerpo = pedir_fn(url)
        if st == 200 and aguja in cuerpo.lower().replace(" ", ""):
            return True, "ok"
        detalle = f"HTTP {st}" if st else f"sin respuesta ({cuerpo})"
        if st == 200:
            detalle = "responde, pero sin el contenido esperado"
        if intento == 0 and espera_s:
            time.sleep(espera_s)
    return False, detalle


def _hora(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%d/%m %H:%M UTC")


def _texto_caida(c: dict, detalle: str, desde: float, recordatorio_min: Optional[int] = None) -> tuple[str, str]:
    if recordatorio_min is None:
        titulo = f"{c['nombre'][0].upper() + c['nombre'][1:]} no responde"
    else:
        titulo = f"{c['nombre'][0].upper() + c['nombre'][1:]} sigue sin responder ({recordatorio_min} min)"
    cuerpo = (f"Qué pasa: no responde desde las {_hora(desde)} ({detalle}).\n"
              f"Qué afecta: {c['afecta']}\n"
              f"Qué hacer: {c['hacer']}\n"
              f"Dirección: {c['url']}")
    return titulo, cuerpo


def decidir(estado: dict, resultados: dict[str, tuple[bool, str]], ahora: float,
            checks: Optional[list[dict]] = None) -> list[tuple[str, str, bool, str]]:
    """Actualiza ``estado`` y devuelve los avisos a mandar: (título, cuerpo, urgente, producto). Solo mira las
    comprobaciones que vienen en ``resultados``. Primer aviso tras ``FALLOS_PARA_AVISAR`` pasadas seguidas
    fallando; luego un recordatorio cada 30 min; y un aviso de vuelta con cuánto duró (solo si hubo aviso de caída)."""
    avisos = []
    for c in (activas() if checks is None else checks):
        cid = c["id"]
        if cid not in resultados:
            continue
        ok, detalle = resultados[cid]
        e = estado.setdefault(cid, {})
        e.setdefault("fallos_seguidos", 0)
        e.setdefault("caido_desde", None)
        e.setdefault("ultimo_aviso", None)
        if not ok:
            if e["caido_desde"] is None:
                e["caido_desde"] = ahora
            e["fallos_seguidos"] += 1
            if e["ultimo_aviso"] is None:
                if e["fallos_seguidos"] >= FALLOS_PARA_AVISAR:
                    e["ultimo_aviso"] = ahora
                    t, b = _texto_caida(c, detalle, e["caido_desde"])
                    avisos.append((t, b, True, c["producto"]))
            elif ahora - e["ultimo_aviso"] >= RECORDATORIO_S:
                e["ultimo_aviso"] = ahora
                minutos = int((ahora - e["caido_desde"]) // 60)
                t, b = _texto_caida(c, detalle, e["caido_desde"], minutos)
                avisos.append((t, b, True, c["producto"]))
        else:
            if e["ultimo_aviso"] is not None:  # solo avisa de la vuelta si avisó de la caída
                minutos = max(1, int((ahora - e["caido_desde"]) // 60))
                n = c["nombre"][0].upper() + c["nombre"][1:]
                avisos.append((f"{n} vuelve a responder",
                               f"Qué pasa: ya responde otra vez. Ha estado caído unos {minutos} min "
                               f"(desde las {_hora(e['caido_desde'])}).\nQué hacer: nada, solo comprobar que todo "
                               f"va bien.", False, c["producto"]))
            e["fallos_seguidos"], e["caido_desde"], e["ultimo_aviso"] = 0, None, None
    return avisos


def _post_json(url: str, datos: dict, cabeceras: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(datos).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          # Resend rechaza con 403 el agente por defecto de urllib.
                                          "User-Agent": "vigilante-externo/1.0", **cabeceras})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


def mandar(titulo: str, cuerpo: str, urgente: bool, producto: str = "HeyDaddy",
           post: Callable = _post_json, entorno: Optional[dict] = None) -> dict:
    env = os.environ if entorno is None else entorno
    hora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    out = {"telegram": None, "correo": None}
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if token and chat:  # Telegram es opcional: sin sus secretos, solo correo
        texto = (f"{'🔴' if urgente else '🟢'} {producto} · {'urgente' if urgente else 'resuelto'}\n\n"
                 f"{titulo}\n\n{cuerpo}\n\n{hora} · vigilante externo")
        out["telegram"] = post(f"https://api.telegram.org/bot{token}/sendMessage",
                               {"chat_id": chat, "text": texto, "disable_web_page_preview": True,
                                "disable_notification": not urgente}, {})
    clave = env.get("RESEND_API_KEY")
    if clave:
        asunto = f"[{producto}]{' URGENTE:' if urgente else ''} {titulo}"
        texto = (f"{producto} · {'urgente' if urgente else 'resuelto'}\n\n{titulo}\n\n{cuerpo}\n\n{hora}\n"
                 f"Vigilante externo (GitHub Actions).")
        out["correo"] = post("https://api.resend.com/emails", {
            "from": env.get("EMAIL_FROM") or REMITENTE_POR_DEFECTO,
            "to": [env.get("ALERT_EMAIL") or DESTINATARIO_POR_DEFECTO],
            "subject": asunto, "text": texto}, {"Authorization": f"Bearer {clave}"})
    return out


def pasada(checks: list[dict]) -> dict[str, tuple[bool, str]]:
    """Comprueba todas a la vez (cada una con sus dos intentos)."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        futuros = {c["id"]: pool.submit(comprobar, c["url"], c["aguja"]) for c in checks}
        return {cid: f.result() for cid, f in futuros.items()}


def tabla(checks: list[dict], resultados: dict[str, tuple[bool, str]]) -> str:
    filas = [f"{'Producto':<24}{'Comprobación':<52}{'Resultado':<10}Detalle"]
    for c in checks:
        ok, det = resultados[c["id"]]
        filas.append(f"{c['producto']:<24}{c['nombre'][:50]:<52}{'OK' if ok else 'FALLA':<10}{det}  {c['url']}")
    return "\n".join(filas)


def main(args: list[str]) -> int:
    checks = activas()
    if "--sin-avisos" in args:
        print(tabla(checks, pasada(checks)))
        return 0
    ruta_estado = next((a for a in args if not a.startswith("--")), "estado.json")
    try:
        with open(ruta_estado, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:  # noqa: BLE001
        estado = {}
    resultados = pasada(checks)
    avisos = decidir(estado, resultados, time.time(), checks)
    for titulo, cuerpo, urgente, producto in avisos:
        print(f"aviso: [{producto}] {titulo} -> {mandar(titulo, cuerpo, urgente, producto)}")
    estado["ultima_pasada"] = datetime.now(timezone.utc).isoformat()
    with open(ruta_estado, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=1)
    print(json.dumps({k: v[0] for k, v in resultados.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
