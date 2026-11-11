"""Pruebas del vigilante (solo librería estándar, sin red ni envíos): python -m unittest -v"""
import unittest

import vigia

C = {"id": "t1", "producto": "Prueba", "nombre": "la web de prueba", "url": "https://ejemplo.test/",
     "aguja": "x", "afecta": "Nadie entra.", "hacer": "Mira el servidor."}
CAE, VIVE = (False, "HTTP 503"), (True, "ok")
T0 = 1_000_000.0


def paso(estado, res, t):
    return vigia.decidir(estado, {"t1": res}, t, [C])


class Decision(unittest.TestCase):
    def test_una_sola_pasada_fallando_no_avisa(self):
        e = {}
        self.assertEqual(paso(e, CAE, T0), [])
        self.assertEqual(paso(e, VIVE, T0 + 300), [])  # se recupera sin aviso ni aviso de vuelta
        self.assertEqual(e["t1"]["fallos_seguidos"], 0)

    def test_cae_tras_n_fallos_seguidos_y_avisa_una_vez(self):
        e = {}
        avisos = []
        for i in range(vigia.FALLOS_PARA_AVISAR + 3):
            avisos += paso(e, CAE, T0 + 300 * i)
        self.assertEqual(len(avisos), 1)  # un solo aviso por caída (los recordatorios tardan 30 min)
        self.assertTrue(avisos[0][2])
        self.assertEqual(avisos[0][3], "Prueba")

    def test_fallo_intermitente_no_acumula(self):
        e = {}
        for i, r in enumerate([CAE, VIVE, CAE, VIVE, CAE]):
            self.assertEqual(paso(e, r, T0 + 300 * i), [])

    def test_aviso_de_vuelta_con_duracion(self):
        e = {}
        for i in range(vigia.FALLOS_PARA_AVISAR):
            paso(e, CAE, T0 + 300 * i)
        vuelta = paso(e, VIVE, T0 + 300 * 4)  # 20 min tras el primer fallo
        self.assertEqual(len(vuelta), 1)
        self.assertFalse(vuelta[0][2])
        self.assertIn("vuelve a responder", vuelta[0][0])
        self.assertIn("20 min", vuelta[0][1])
        self.assertEqual(paso(e, VIVE, T0 + 300 * 5), [])  # y no se repite

    def test_recordatorio_cada_30_min(self):
        e = {}
        n = 0
        for i in range(14):  # 70 min caído
            n += len(paso(e, CAE, T0 + 300 * i))
        self.assertEqual(n, 3)  # primer aviso + recordatorios a +30 y +60 min del aviso

    def test_texto_del_aviso_en_llano(self):
        e = {}
        avisos = []
        for i in range(vigia.FALLOS_PARA_AVISAR):
            avisos += paso(e, CAE, T0 + 300 * i)
        titulo, cuerpo = avisos[0][0], avisos[0][1]
        self.assertEqual(titulo, "La web de prueba no responde")
        for frag in ("Qué pasa:", "Qué afecta: Nadie entra.", "Qué hacer: Mira el servidor.", "HTTP 503",
                     "https://ejemplo.test/", "UTC"):
            self.assertIn(frag, cuerpo)


class Envio(unittest.TestCase):
    def setUp(self):
        self.llamadas = []
        self.post = lambda url, datos, cab: (self.llamadas.append((url, datos, cab)) or 200)

    def test_sin_telegram_no_falla_y_manda_correo_con_valores_por_defecto(self):
        out = vigia.mandar("X cae", "cuerpo", True, "Prueba", self.post, {"RESEND_API_KEY": "k-falsa"})
        self.assertIsNone(out["telegram"])
        self.assertEqual(out["correo"], 200)
        url, datos, cab = self.llamadas[0]
        self.assertIn("resend.com", url)
        self.assertEqual(datos["from"], "Vigilante <avisos@getoriginl.com>")
        self.assertEqual(datos["to"], ["julian@ghgroup.es"])
        self.assertEqual(datos["subject"], "[Prueba] URGENTE: X cae")

    def test_remitente_y_destinatario_configurables(self):
        vigia.mandar("t", "c", False, "P", self.post,
                     {"RESEND_API_KEY": "k", "EMAIL_FROM": "A <a@b.c>", "ALERT_EMAIL": "z@z.z"})
        datos = self.llamadas[0][1]
        self.assertEqual((datos["from"], datos["to"], datos["subject"]), ("A <a@b.c>", ["z@z.z"], "[P] t"))

    def test_con_telegram_manda_los_dos(self):
        out = vigia.mandar("t", "c", True, "P", self.post,
                           {"RESEND_API_KEY": "k", "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "1"})
        self.assertEqual((out["telegram"], out["correo"]), (200, 200))

    def test_sin_ningun_secreto_no_falla_ni_envia(self):
        out = vigia.mandar("t", "c", True, "P", self.post, {})
        self.assertEqual(out, {"telegram": None, "correo": None})
        self.assertEqual(self.llamadas, [])


class Lista(unittest.TestCase):
    def test_editor_se_omite_sin_variable_y_se_incluye_con_ella(self):
        sin = [c["id"] for c in vigia.activas(entorno={})]
        self.assertNotIn("originl_editor", sin)
        con = {c["id"]: c for c in vigia.activas(entorno={"ORIGINL_EDITOR_URL": "https://m.test/"})}
        self.assertEqual(con["originl_editor"]["url"], "https://m.test/entrar")
        self.assertNotIn("originl_editor", [c["id"] for c in vigia.activas(entorno={"ORIGINL_EDITOR_URL": "http://x"})])

    def test_ids_unicos_y_todos_con_producto_y_texto_de_ayuda(self):
        cs = vigia.activas(entorno={"ORIGINL_EDITOR_URL": "https://m.test"})
        ids = [c["id"] for c in cs]
        self.assertEqual(len(ids), len(set(ids)))
        for c in cs:
            self.assertTrue(c["producto"] and c["afecta"] and c["hacer"] and c["url"].startswith("https://"))

    def test_comprobar_dos_intentos_y_aguja(self):
        seq = iter([(500, ""), (200, "<html>Hola X</html>")])
        self.assertEqual(vigia.comprobar("u", "x", lambda u: next(seq), 0), (True, "ok"))
        self.assertEqual(vigia.comprobar("u", "zzz", lambda u: (200, "hola"), 0)[0], False)


if __name__ == "__main__":
    unittest.main()
