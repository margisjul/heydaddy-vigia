"""Vigilante EXTERNO de HeyDaddy (22-oct-2026): comprueba cada 5 minutos, desde fuera de Railway, que la
web y la API responden, y si no, avisa a Julián por Telegram y por correo con "HeyDaddy" delante.

Corre en GitHub Actions (repositorio público margisjul/heydaddy-vigia, minutos gratis), así que sigue
avisando aunque Railway entero se caiga o retire los despliegues (como el 19-oct-2026). Solo librería
estándar. El estado (caído o no, desde cuándo, último aviso) viaja entre ejecuciones en un JSON.

Variables (secretos del repositorio):
  TELEGRAM_BOT_TOKEN   token del bot de avisos de HeyDaddy (o del bot de admin)
  TELEGRAM_CHAT_ID     chat de Julián
  RESEND_API_KEY       clave de Resend (solo envío)
  ALERT_EMAIL          a quién (por defecto julian@ghgroup.es)
  EMAIL_FROM           remitente (por defecto "HeyDaddy <no-reply@heydaddy.app>")
Uso: python vigia.py <fichero_estado.json>
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

COMPROBACIONES = [
    # (id, nombre visible, url, texto que tiene que aparecer en la respuesta)
    ("web", "la web (www.heydaddy.app)", "https://www.heydaddy.app/login", "heydaddy"),
    ("api", "la API (servidor)", "https://heydaddy-production.up.railway.app/health", '"status":"ok"'),
    ("api_web", "la API a través de la web", "https://www.heydaddy.app/api/health", '"status":"ok"'),
]
RECORDATORIO_S = 30 * 60  # mientras siga caído, un recordatorio cada 30 min


def pedir(url: str, timeout: float = 15.0) -> tuple[Optional[int], str]:
    req = urllib.request.Request(url, headers={"User-Agent": "heydaddy-vigia/1.0"})
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
    for intento in range(2):
        st, cuerpo = pedir_fn(url)
        if st == 200 and aguja.lower() in cuerpo.lower().replace(" ", ""):
            return True, "ok"
        detalle = f"HTTP {st}" if st else f"sin respuesta ({cuerpo})"
        if st == 200:
            detalle = "responde, pero sin el contenido esperado"
        if intento == 0 and espera_s:
            time.sleep(espera_s)
    return False, detalle


def decidir(estado: dict, resultados: dict[str, tuple[bool, str]], ahora: float) -> list[tuple[str, str, bool]]:
    """Actualiza ``estado`` y devuelve los avisos a mandar: (título, cuerpo, urgente)."""
    avisos = []
    for cid, nombre, url, _ in COMPROBACIONES:
        ok, detalle = resultados[cid]
        e = estado.setdefault(cid, {"caido_desde": None, "ultimo_aviso": None})
        if not ok:
            if e["caido_desde"] is None:
                e["caido_desde"], e["ultimo_aviso"] = ahora, ahora
                avisos.append((f"{nombre} no responde", f"{url}\n{detalle}", True))
            elif ahora - (e["ultimo_aviso"] or 0) >= RECORDATORIO_S:
                minutos = int((ahora - e["caido_desde"]) // 60)
                e["ultimo_aviso"] = ahora
                avisos.append((f"{nombre} sigue sin responder ({minutos} min)", f"{url}\n{detalle}", True))
        elif e["caido_desde"] is not None:
            minutos = int((ahora - e["caido_desde"]) // 60)
            e["caido_desde"], e["ultimo_aviso"] = None, None
            avisos.append((f"{nombre} vuelve a responder", f"Ha estado caída unos {minutos} min.", False))
    return avisos


def _post_json(url: str, datos: dict, cabeceras: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(datos).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **cabeceras})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


def mandar(titulo: str, cuerpo: str, urgente: bool) -> dict:
    hora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    out = {"telegram": None, "correo": None}
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        texto = (f"{'🔴' if urgente else '🟢'} HeyDaddy · {'urgente' if urgente else 'resuelto'}\n\n"
                 f"{titulo}\n\n{cuerpo}\n\n{hora} · vigilante externo")
        out["telegram"] = _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                                     {"chat_id": chat, "text": texto, "disable_web_page_preview": True,
                                      "disable_notification": not urgente}, {})
    clave = os.environ.get("RESEND_API_KEY")
    if clave:
        asunto = f"[HeyDaddy]{' URGENTE:' if urgente else ''} {titulo}"
        texto = f"HeyDaddy · {'urgente' if urgente else 'resuelto'}\n\n{titulo}\n\n{cuerpo}\n\n{hora}\nVigilante externo (GitHub Actions)."
        out["correo"] = _post_json("https://api.resend.com/emails", {
            "from": os.environ.get("EMAIL_FROM") or "HeyDaddy <no-reply@heydaddy.app>",
            "to": [os.environ.get("ALERT_EMAIL") or "julian@ghgroup.es"],
            "subject": asunto, "text": texto}, {"Authorization": f"Bearer {clave}"})
    return out


def main(ruta_estado: str) -> int:
    try:
        with open(ruta_estado, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:  # noqa: BLE001
        estado = {}
    resultados = {cid: comprobar(url, aguja) for cid, _, url, aguja in COMPROBACIONES}
    avisos = decidir(estado, resultados, time.time())
    for titulo, cuerpo, urgente in avisos:
        print(f"aviso: {titulo} -> {mandar(titulo, cuerpo, urgente)}")
    estado["ultima_pasada"] = datetime.now(timezone.utc).isoformat()
    with open(ruta_estado, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=1)
    print(json.dumps({k: v[0] for k, v in resultados.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "estado.json"))
