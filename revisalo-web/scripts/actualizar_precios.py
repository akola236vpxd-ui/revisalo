#!/usr/bin/env python3
"""Revísalo · descarga los precios de la tarifa regulada (PVPC) de Red Eléctrica
y actualiza datos/precios.json. Lo ejecuta GitHub cada día; no hace falta tocarlo.

Fuente principal: apidatos.ree.es (datos abiertos de REE).
Fuente de reserva: api.esios.ree.es/archives/70 (fichero diario del PVPC).
Los precios se guardan en €/kWh SIN impuestos; la web añade IVA e impuesto eléctrico.
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
AQUI = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.normpath(os.path.join(AQUI, "..", "datos", "precios.json"))
DIAS_GUARDADOS = 45
CABECERAS = {"User-Agent": "Revisalo/1.0 (calculadora de luz)", "Accept": "application/json"}

# Impuestos vigentes. Si cambian, se actualizan aquí y la web los usa sola.
IMPUESTOS = {"iva": 0.21, "iee": 0.0511269632}


def leer_json(url):
    req = urllib.request.Request(url, headers=CABECERAS)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def agrupar(puntos):
    """puntos: lista de (datetime con zona, €/MWh). Devuelve {fecha: [24 precios €/kWh]}.
    Sirve para datos por horas o por cuartos de hora. En los días de cambio de hora
    (23 o 25 horas) se rellena o se promedia la hora repetida."""
    por_dia = {}
    for momento, valor in puntos:
        local = momento.astimezone(TZ)
        por_dia.setdefault(local.date().isoformat(), {}).setdefault(local.hour, []).append(valor)
    salida = {}
    for dia, horas in por_dia.items():
        if len(horas) < 23:
            continue  # día incompleto: todavía no publicado entero
        fila = []
        for h in range(24):
            vals = horas.get(h) or horas.get(h - 1) or horas.get(h + 1)
            fila.append(round(sum(vals) / len(vals) / 1000, 5))
        if all(-0.5 < v < 2 for v in fila):
            salida[dia] = fila
    return salida


def desde_apidatos(inicio, fin):
    url = ("https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
           f"?start_date={inicio.isoformat()}T00:00&end_date={fin.isoformat()}T23:59&time_trunc=hour")
    datos = leer_json(url)
    puntos = []
    for serie in datos.get("included", []):
        attrs = serie.get("attributes") or {}
        titulo = (attrs.get("title") or serie.get("type") or "").upper()
        if "PVPC" not in titulo:
            continue
        for v in attrs.get("values", []):
            if v.get("value") is None or not v.get("datetime"):
                continue
            puntos.append((dt.datetime.fromisoformat(v["datetime"]), float(v["value"])))
    return agrupar(puntos)


def numero_es(texto):
    texto = str(texto).strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return float(texto)


def desde_esios(dia):
    url = f"https://api.esios.ree.es/archives/70/download_json?locale=es&date={dia.isoformat()}"
    datos = leer_json(url)
    puntos = []
    vistos = {}
    for fila in datos.get("PVPC", []):
        valor = fila.get("PCB") or fila.get("GEN")
        if valor in (None, ""):
            continue
        d, m, a = (int(x) for x in fila["Dia"].split("/"))
        hora = min(int(str(fila["Hora"])[:2]), 23)
        # en el día de 25 horas la misma hora aparece dos veces
        n = vistos.get(hora, 0)
        vistos[hora] = n + 1
        momento = dt.datetime(a, m, d, hora, tzinfo=TZ) + dt.timedelta(minutes=n)
        puntos.append((momento, numero_es(valor)))
    return agrupar(puntos)


def main():
    hoy = dt.datetime.now(TZ).date()
    ayer, manana = hoy - dt.timedelta(days=1), hoy + dt.timedelta(days=1)

    try:
        with open(RUTA, encoding="utf-8") as f:
            actual = json.load(f)
    except (OSError, ValueError):
        actual = {}
    dias = dict(actual.get("dias") or {})

    nuevos = {}
    try:
        nuevos.update(desde_apidatos(ayer, manana))
        print("apidatos:", sorted(nuevos))
    except Exception as e:  # noqa: BLE001
        print("apidatos falló:", e, file=sys.stderr)

    for dia in (ayer, hoy, manana):
        if dia.isoformat() in nuevos:
            continue
        try:
            extra = desde_esios(dia)
            nuevos.update({k: v for k, v in extra.items() if k == dia.isoformat()})
            print("esios", dia, "ok" if dia.isoformat() in extra else "sin datos")
        except Exception as e:  # noqa: BLE001
            print("esios", dia, "falló:", e, file=sys.stderr)

    dias.update(nuevos)
    limite = (hoy - dt.timedelta(days=DIAS_GUARDADOS)).isoformat()
    dias = {k: dias[k] for k in sorted(dias) if k >= limite}

    if dias == (actual.get("dias") or {}) and actual.get("impuestos") == IMPUESTOS:
        print("Sin cambios.")
        return 0

    salida = {
        "actualizado": dt.datetime.now(TZ).isoformat(timespec="minutes"),
        "fuente": "Red Eléctrica de España · PVPC 2.0TD (península, Baleares y Canarias)",
        "unidad": "€/kWh sin impuestos",
        "impuestos": IMPUESTOS,
        "dias": dias,
    }
    os.makedirs(os.path.dirname(RUTA), exist_ok=True)
    with open(RUTA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1)
    print("Guardado:", RUTA, "· días:", ", ".join(sorted(nuevos)) or "ninguno nuevo")
    if hoy.isoformat() not in dias:
        print("Aviso: todavía no hay precios de hoy.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
