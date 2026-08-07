"""
Piloto de geocodificación de las VUT de Galicia contra el Catastro, por dirección.

Galicia no publica referencia catastral (a diferencia de Valencia) ni coordenadas para el
99,3 % de sus VUT, así que la única vía es la dirección postal. Este script NO procesa el
lote completo: toma una muestra aleatoria y mide qué porcentaje se resuelve, para decidir
si merece la pena lanzar los 28.253 restantes o hace falta otra estrategia.

Cadena de tres pasos por dirección:

  1. ConsultaMunicipio  — catálogo de municipios de la provincia (se cachea, 4 llamadas).
     El registro escribe "O CAMPO LAMEIRO" y el Catastro "CAMPO LAMEIRO", así que hay que
     casar nombres en vez de enviarlos tal cual.
  2. ConsultaVia        — busca la vía por nombre aproximado y devuelve su tipo y nombre
     exactos. Imprescindible: las direcciones gallegas son topónimos ("LUGAR DO PAZO") que
     el Catastro guarda como "LG PAZO".
  3. Consulta_DNPLOC    — devuelve la referencia catastral de ese portal.
  4. Consulta_CPMRC     — referencia catastral -> coordenadas (se reutiliza de
     geocode_catastro_valencia.py, con la misma reproyección a WGS84).

Uso:
    python etl/transform/geocode_catastro_galicia_piloto.py
    python etl/transform/geocode_catastro_galicia_piloto.py --muestra 300 --semilla 42
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geocode_catastro_valencia import a_wgs84  # misma reproyección a WGS84

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CACHE_DIR = PROCESSED_DIR / "geocache"

ENTRADA = PROCESSED_DIR / "vut_normalizado_galicia.csv"
CACHE_PATH = CACHE_DIR / "catastro_galicia_piloto.jsonl"
SALIDA = PROCESSED_DIR / "vut_galicia_piloto_geocodificado.csv"

CALLEJERO = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx"
COORDS = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx"
NS = {"c": "http://www.catastro.meh.es/"}
UA = {"User-Agent": "TFM-TUI-Dashboard/0.1 (proyecto academico TFM)"}

DELAY = 0.25
TIMEOUT = 30
CAJA_GALICIA = (41.7, 43.9, -9.4, -6.7)

ARTICULOS = ("a", "o", "as", "os", "la", "el", "las", "los", "l")

# Genéricos que el registro antepone y que el Catastro guarda como sigla aparte.
GENERICOS = {
    "lugar": "LG", "lg": "LG", "aldea": "LG", "barrio": "BO", "bo": "BO",
    "calle": "CL", "rua": "CL", "cl": "CL", "c": "CL",
    "avenida": "AV", "avda": "AV", "av": "AV",
    "plaza": "PZ", "praza": "PZ", "pz": "PZ",
    "carretera": "CR", "estrada": "CR", "cr": "CR", "ctra": "CR",
    "camino": "CM", "camino_": "CM", "camiño": "CM",
    "travesia": "TR", "travesia_": "TR",
    "paseo": "PS", "urbanizacion": "UR", "poligono": "PG",
}

RE_NUMERO = re.compile(r"\bn[ºo°]?\s*\.?\s*(\d+)", re.IGNORECASE)
RE_NUMERO_SUELTO = re.compile(r",\s*(\d+)\s*(?:,|$)")
RE_PARENTESIS = re.compile(r"\([^)]*\)")

# El registro gallego escribe "<vía> Nº <n>, PISO 4º LETRA A". Todo lo que va detrás del
# número es planta y puerta: si no se corta, el nombre de vía que se envía al Catastro
# queda como "DOCTOR GONZALEZ SIERRA PISO 5O LETRA A" y no encuentra nada.
RE_COLA = re.compile(
    r"\b(piso|planta|letra|puerta|porta|escalera|esc|bloque|bl|portal|baixo|bajo|entlo|"
    r"entresuelo|atico|sotano|pta|izq|izda|dcha|der)\b.*$",
    re.IGNORECASE,
)


def normalizar(texto: object) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def sin_articulos(texto: object) -> str:
    return " ".join(p for p in normalizar(texto).split() if p not in ARTICULOS)


# ---------------------------------------------------------------------------
# Caché
# ---------------------------------------------------------------------------

def cargar_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    cache = {}
    with CACHE_PATH.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                reg = json.loads(linea)
            except ValueError:
                continue
            cache[reg["clave"]] = reg
    return cache


def anexar_cache(registro: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------

def get_xml(url: str, params: dict, sesion: requests.Session) -> ET.Element | None:
    try:
        r = sesion.get(url, params=params, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        return ET.fromstring(r.text)
    except (requests.RequestException, ET.ParseError):
        return None


def error_de(raiz: ET.Element | None) -> str | None:
    if raiz is None:
        return "sin respuesta"
    err = raiz.find(".//c:lerr/c:err/c:des", NS)
    return (err.text or "").strip() if err is not None else None


def municipios_de(provincia: str, sesion: requests.Session, memo: dict) -> list[str]:
    if provincia in memo:
        return memo[provincia]
    raiz = get_xml(f"{CALLEJERO}/ConsultaMunicipio",
                   {"Provincia": provincia, "Municipio": ""}, sesion)
    nombres = [e.text for e in raiz.findall(".//c:nm", NS)] if raiz is not None else []
    memo[provincia] = nombres
    time.sleep(DELAY)
    return nombres


def casar_municipio(municipio: str, catalogo: list[str]) -> str | None:
    """El registro y el Catastro colocan el artículo de forma distinta."""
    objetivo = sin_articulos(municipio)
    for nombre in catalogo:
        if sin_articulos(nombre) == objetivo:
            return nombre
    for nombre in catalogo:
        if objetivo and objetivo in sin_articulos(nombre):
            return nombre
    return None


def descomponer(direccion: str) -> tuple[str, str, str]:
    """Devuelve (sigla, nombre_via, numero) a partir de la dirección del registro."""
    s = RE_PARENTESIS.sub(" ", str(direccion))

    numero = ""
    m = RE_NUMERO.search(s) or RE_NUMERO_SUELTO.search(s)
    if m:
        numero = m.group(1)
        # La vía es lo que precede al número; lo de después es planta y puerta.
        s = s[: m.start()]
    else:
        s = RE_COLA.sub(" ", s)

    s = RE_COLA.sub(" ", s)
    s = re.sub(r"\bs/?n\b", " ", s, flags=re.IGNORECASE)

    palabras = normalizar(s).split()
    sigla = ""
    while palabras and palabras[0] in GENERICOS:
        sigla = sigla or GENERICOS[palabras[0]]
        palabras.pop(0)
    # 'LUGAR DO PAZO' -> quitar tambien la preposicion que queda suelta
    while palabras and palabras[0] in ("de", "do", "da", "dos", "das", "del"):
        palabras.pop(0)

    return sigla, " ".join(palabras).upper(), numero


def variantes_via(nombre: str) -> list[str]:
    """
    Nombres a probar, de más a menos específico.

    El registro y el Catastro no escriben igual los tratamientos ni los genéricos
    encadenados: "DOCTOR GONZÁLEZ SIERRA" puede estar como "GONZALEZ SIERRA", y
    "PLAYA PASEO DE SILGAR" como "SILGAR". Acortar por la izquierda cubre ambos casos sin
    inventar nada, porque la coincidencia final se sigue exigiendo sobre el nombre devuelto.
    """
    palabras = nombre.split()
    variantes = [nombre]
    if len(palabras) > 1:
        variantes.append(" ".join(palabras[1:]))
    if len(palabras) > 2:
        variantes.append(" ".join(palabras[-2:]))
        variantes.append(palabras[-1])
    vistas, unicas = set(), []
    for v in variantes:
        if v and v not in vistas:
            vistas.add(v)
            unicas.append(v)
    return unicas


def buscar_via(prov: str, mun: str, sigla: str, nombre: str,
               sesion: requests.Session) -> tuple[str, str] | None:
    """Devuelve (tipo_via, nombre_via) exactos del Catastro, o None."""
    if not nombre:
        return None

    for variante in variantes_via(nombre):
        via = _buscar_via_exacta(prov, mun, sigla, variante, sesion)
        if via:
            return via
    return None


def _buscar_via_exacta(prov: str, mun: str, sigla: str, nombre: str,
                       sesion: requests.Session) -> tuple[str, str] | None:
    raiz = get_xml(f"{CALLEJERO}/ConsultaVia",
                   {"Provincia": prov, "Municipio": mun, "TipoVia": "", "NombreVia": nombre},
                   sesion)
    time.sleep(DELAY)
    if raiz is None or error_de(raiz):
        return None

    candidatas = []
    for calle in raiz.findall(".//c:calle", NS):
        tv = calle.findtext("c:dir/c:tv", "", NS)
        nv = calle.findtext("c:dir/c:nv", "", NS)
        if nv:
            candidatas.append((tv, nv))
    if not candidatas:
        return None

    objetivo = normalizar(nombre)
    # 1) coincidencia exacta de nombre y, si la conocemos, de sigla
    for tv, nv in candidatas:
        if normalizar(nv) == objetivo and (not sigla or tv == sigla):
            return tv, nv
    for tv, nv in candidatas:
        if normalizar(nv) == objetivo:
            return tv, nv
    # 2) una sola candidata: se acepta
    if len(candidatas) == 1:
        return candidatas[0]
    # Varias parciales y ninguna exacta: es ambiguo, mejor no inventar.
    return None


def resolver_direccion(fila: pd.Series, sesion: requests.Session, memo: dict) -> dict:
    clave = f"{fila['provincia']}|{fila['municipio']}|{fila['direccion']}"
    reg = {"clave": clave, "lat": None, "lon": None, "rc": None,
           "motivo": None, "fecha": datetime.now(timezone.utc).isoformat()}

    if not str(fila.get("direccion", "")).strip() or str(fila["direccion"]) == "nan":
        reg["motivo"] = "sin direccion en origen"
        return reg

    catalogo = municipios_de(str(fila["provincia"]), sesion, memo)
    if not catalogo:
        reg["motivo"] = "no se pudo obtener el catalogo de municipios"
        return reg

    mun = casar_municipio(str(fila["municipio"]), catalogo)
    if not mun:
        reg["motivo"] = "municipio no encontrado en el Catastro"
        return reg

    sigla, nombre, numero = descomponer(fila["direccion"])
    if not nombre:
        reg["motivo"] = "no se pudo extraer nombre de via"
        return reg

    via = buscar_via(str(fila["provincia"]), mun, sigla, nombre, sesion)
    if not via:
        reg["motivo"] = "via no encontrada o ambigua"
        return reg
    tv, nv = via

    if not numero:
        reg["motivo"] = "via encontrada pero sin numero de portal"
        return reg

    raiz = get_xml(f"{CALLEJERO}/Consulta_DNPLOC",
                   {"Provincia": fila["provincia"], "Municipio": mun, "Sigla": tv,
                    "Calle": nv, "Numero": numero, "Bloque": "", "Escalera": "",
                    "Planta": "", "Puerta": ""}, sesion)
    time.sleep(DELAY)
    err = error_de(raiz)
    if err:
        reg["motivo"] = f"DNPLOC: {err}"
        return reg

    pc1 = raiz.find(".//c:rc/c:pc1", NS)
    pc2 = raiz.find(".//c:rc/c:pc2", NS)
    if pc1 is None or pc2 is None:
        reg["motivo"] = "DNPLOC sin referencia catastral"
        return reg
    rc14 = f"{pc1.text}{pc2.text}"
    reg["rc"] = rc14

    raiz = get_xml(f"{COORDS}/Consulta_CPMRC",
                   {"Provincia": "", "Municipio": "", "SRS": "EPSG:4326", "RC": rc14}, sesion)
    time.sleep(DELAY)
    err = error_de(raiz)
    if err:
        reg["motivo"] = f"CPMRC: {err}"
        return reg

    x = raiz.find(".//c:coord/c:geo/c:xcen", NS)
    y = raiz.find(".//c:coord/c:geo/c:ycen", NS)
    srs = raiz.find(".//c:coord/c:geo/c:srs", NS)
    if x is None or y is None:
        reg["motivo"] = "CPMRC sin coordenadas"
        return reg

    lat, lon = a_wgs84(float(x.text), float(y.text), srs.text if srs is not None else "")
    if not (CAJA_GALICIA[0] <= lat <= CAJA_GALICIA[1] and CAJA_GALICIA[2] <= lon <= CAJA_GALICIA[3]):
        reg["motivo"] = "coordenada fuera de Galicia"
        return reg

    reg["lat"], reg["lon"] = lat, lon
    return reg


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Piloto: geocodifica una muestra de VUT de Galicia contra el Catastro."
    )
    parser.add_argument("--muestra", type=int, default=300)
    parser.add_argument("--semilla", type=int, default=42)
    args = parser.parse_args()

    if not ENTRADA.exists():
        print(f"ERROR: falta {ENTRADA}", file=sys.stderr)
        return 1

    df = pd.read_csv(ENTRADA, low_memory=False)
    sin_coord = df[df["lat"].isna()]
    print(f"Galicia: {len(df):,} registros, {len(sin_coord):,} sin coordenadas")

    muestra = sin_coord.sample(n=min(args.muestra, len(sin_coord)), random_state=args.semilla)
    print(f"Muestra aleatoria: {len(muestra)} (semilla {args.semilla})\n")

    cache = cargar_cache()
    sesion = requests.Session()
    memo: dict[str, list[str]] = {}
    inicio = time.time()
    resultados = []

    for i, (_, fila) in enumerate(muestra.iterrows(), start=1):
        clave = f"{fila['provincia']}|{fila['municipio']}|{fila['direccion']}"
        if clave in cache:
            resultados.append(cache[clave])
            continue
        reg = resolver_direccion(fila, sesion, memo)
        cache[clave] = reg
        anexar_cache(reg)
        resultados.append(reg)
        if i % 50 == 0:
            ok = sum(1 for r in resultados if r["lat"] is not None)
            print(f"  [{i}/{len(muestra)}] {ok} resueltas ({100 * ok / len(resultados):.0f}%)")

    muestra = muestra.copy()
    muestra["lat_catastro"] = [r["lat"] for r in resultados]
    muestra["lon_catastro"] = [r["lon"] for r in resultados]
    muestra["catastro_rc"] = [r["rc"] for r in resultados]
    muestra["catastro_motivo"] = [r["motivo"] for r in resultados]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    muestra.to_csv(SALIDA, index=False, encoding="utf-8")

    # ---------------- Resumen ----------------
    ok = int(muestra["lat_catastro"].notna().sum())
    total = len(muestra)
    duracion = time.time() - inicio

    print("\n" + "=" * 72)
    print("PILOTO — CATASTRO / GALICIA POR DIRECCIÓN")
    print("=" * 72)
    print(f"  Muestra:        {total:>6,}")
    print(f"  Geocodificadas: {ok:>6,}  ({100 * ok / total:.1f} %)")
    print(f"  Sin resolver:   {total - ok:>6,}  ({100 * (total - ok) / total:.1f} %)")
    print(f"\n  Duración: {duracion / 60:.1f} min "
          f"({duracion / total:.1f} s por dirección)")
    print(f"  Extrapolado a las 28.253 pendientes: "
          f"{28253 * duracion / total / 3600:.1f} h")

    motivos = Counter(m for m in muestra["catastro_motivo"] if m)
    if motivos:
        print("\n  Motivos de fallo:")
        for motivo, n in motivos.most_common(10):
            print(f"    {n:>5}  ({100 * n / total:>4.1f} %)  {motivo[:58]}")

    fallos = muestra[muestra["lat_catastro"].isna()]
    if len(fallos):
        print("\n  Ejemplos de direcciones no resueltas:")
        for _, f in fallos.head(8).iterrows():
            print(f"    [{str(f['catastro_motivo'])[:34]:<34}] "
                  f"{str(f['direccion'])[:44]:<44} | {f['municipio']}")

    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
