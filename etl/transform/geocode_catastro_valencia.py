"""
Geocodificación de las VUT de la Comunitat Valenciana por referencia catastral.

El registro valenciano no publica coordenadas, pero sí `ref_catastral` en 89.201 de sus
89.978 registros. Resolver la referencia contra el Catastro da la parcela exacta, sin
depender de interpretar direcciones postales: es sustancialmente mejor que Nominatim y, a
este volumen, la única vía razonable (89.978 direcciones a 1 req/s en Nominatim serían 25
horas de un servicio gratuito mantenido por donaciones).

Servicio: OVCCoordenadas.asmx / Consulta_CPMRC (referencia catastral -> coordenadas).
Nota: `Consulta_DNPRC` pertenece a OVCCallejero.asmx y devuelve datos descriptivos, no
coordenadas; el método que da la geometría es Consulta_CPMRC.

Dos detalles del servicio:

  - La referencia del registro valenciano tiene 20 caracteres (14 de parcela + 4 de cargo +
    2 de control). Consulta_CPMRC exige las 14 de parcela; con 20 responde
    "LA REFERENCIA CATASTRAL DEBE SER DE 14 POSICIONES". Se trunca, y el resultado es el
    centroide de la parcela, que es la precisión máxima alcanzable para un piso concreto.
  - Se pide SRS=EPSG:4326 y el servicio lo devuelve ya en ese sistema. Aun así se comprueba
    el SRS de la respuesta y, si llegara en ETRS89 geográfico o en UTM, se reproyecta con
    pyproj antes de guardar nada.

Uso:
    python etl/transform/geocode_catastro_valencia.py --muestra 200
    python etl/transform/geocode_catastro_valencia.py
    python etl/transform/geocode_catastro_valencia.py --solo-resumen
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CACHE_DIR = PROCESSED_DIR / "geocache"

ENTRADA_RAW = RAW_DIR / "vut_oficial_valencia.csv"
ENTRADA_NORM = PROCESSED_DIR / "vut_normalizado_valencia.csv"
CACHE_PATH = CACHE_DIR / "catastro_valencia.jsonl"
SALIDA = PROCESSED_DIR / "vut_valencia_geocodificado.csv"

BASE = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx"
NS = {"c": "http://www.catastro.meh.es/"}
UA = {"User-Agent": "TFM-TUI-Dashboard/0.1 (proyecto academico TFM)"}

# El Catastro no publica un límite explícito, pero es un servicio público gratuito: 4 req/s
# es un ritmo prudente. Subirlo no acelera mucho y aumenta el riesgo de que corten.
DELAY_SEGUNDOS = 0.25
TIMEOUT = 30

# Caja de la Comunitat Valenciana, para detectar coordenadas absurdas.
CAJA_CV = (37.8, 40.8, -1.6, 0.6)  # lat_min, lat_max, lon_min, lon_max


def normalizar(texto: object) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


# Artículos que el registro pospone y el Catastro antepone: el registro escribe
# "ATZÚBIA, L'" donde el Catastro pone "L'ATZUBIA". Es el mismo municipio, así que se
# eliminan de ambos lados antes de comparar; si no, el contraste da falsos negativos.
ARTICULOS = ("l", "el", "la", "els", "les", "es", "sa", "lo", "los", "las")


def nucleo_municipio(texto: object) -> str:
    """Nombre de municipio sin artículos, para comparar entre fuentes."""
    palabras = [p for p in normalizar(texto).split() if p not in ARTICULOS]
    return " ".join(palabras)


# ---------------------------------------------------------------------------
# Caché reanudable
# ---------------------------------------------------------------------------

def cargar_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    cache: dict[str, dict] = {}
    with CACHE_PATH.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                reg = json.loads(linea)
            except ValueError:
                continue  # línea truncada por un corte a media escritura
            cache[reg["rc14"]] = reg
    return cache


def anexar_cache(registro: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Consulta al Catastro
# ---------------------------------------------------------------------------

def a_wgs84(x: float, y: float, srs: str) -> tuple[float, float]:
    """
    Devuelve (lat, lon) en WGS84.

    Se pide EPSG:4326 y es lo que llega, pero si el servicio respondiera en ETRS89
    geográfico o en una de las zonas UTM peninsulares se reproyecta en vez de dar el dato
    por bueno: una coordenada UTM interpretada como grados acabaría en el golfo de Guinea.
    """
    codigo = (srs or "").strip().upper()
    if codigo in ("", "EPSG:4326"):
        return y, x

    from pyproj import Transformer

    if codigo == "EPSG:4258":
        # ETRS89 geográfico: difiere de WGS84 en centímetros, pero se convierte igualmente.
        transformer = Transformer.from_crs("EPSG:4258", "EPSG:4326", always_xy=True)
    else:
        transformer = Transformer.from_crs(codigo, "EPSG:4326", always_xy=True)

    lon, lat = transformer.transform(x, y)
    return lat, lon


def consultar_rc(rc14: str, sesion: requests.Session) -> dict:
    """Resuelve una referencia catastral. Nunca lanza: devuelve el registro con `error`."""
    registro: dict = {"rc14": rc14, "lat": None, "lon": None, "ldt": None,
                      "srs_origen": None, "error": None,
                      "fecha": datetime.now(timezone.utc).isoformat()}
    try:
        r = sesion.get(
            f"{BASE}/Consulta_CPMRC",
            params={"Provincia": "", "Municipio": "", "SRS": "EPSG:4326", "RC": rc14},
            headers=UA, timeout=TIMEOUT,
        )
        r.raise_for_status()
        raiz = ET.fromstring(r.text)
    except (requests.RequestException, ET.ParseError) as exc:
        registro["error"] = f"{type(exc).__name__}: {exc}"
        registro["reintentable"] = True  # fallo de red: no se cachea
        return registro

    err = raiz.find(".//c:lerr/c:err/c:des", NS)
    if err is not None:
        registro["error"] = (err.text or "").strip()
        return registro

    xcen = raiz.find(".//c:coord/c:geo/c:xcen", NS)
    ycen = raiz.find(".//c:coord/c:geo/c:ycen", NS)
    if xcen is None or ycen is None:
        registro["error"] = "respuesta sin coordenadas"
        return registro

    srs_dev = raiz.find(".//c:coord/c:geo/c:srs", NS)
    ldt = raiz.find(".//c:coord/c:ldt", NS)
    try:
        lat, lon = a_wgs84(float(xcen.text), float(ycen.text),
                           srs_dev.text if srs_dev is not None else "")
    except (TypeError, ValueError) as exc:
        registro["error"] = f"coordenada ilegible: {exc}"
        return registro

    registro.update({
        "lat": lat, "lon": lon,
        "srs_origen": srs_dev.text if srs_dev is not None else None,
        "ldt": (ldt.text or "").strip() if ldt is not None else None,
    })
    return registro


def resolver(pendientes: list[str], delay: float) -> dict[str, dict]:
    cache = cargar_cache()
    faltan = [rc for rc in pendientes if rc not in cache]

    print(f"  Parcelas únicas: {len(pendientes):,}")
    print(f"  Ya en caché:     {len(pendientes) - len(faltan):,}")
    print(f"  Pendientes:      {len(faltan):,}")
    if faltan:
        print(f"  Tiempo estimado: {len(faltan) * delay / 60:.0f} min a {1 / delay:.0f} req/s\n")

    sesion = requests.Session()
    aciertos = fallos_red = 0

    for i, rc in enumerate(faltan, start=1):
        registro = consultar_rc(rc, sesion)

        if registro.get("reintentable"):
            # No se cachea: en la siguiente ejecución se vuelve a intentar.
            fallos_red += 1
            if fallos_red % 20 == 1:
                print(f"  [{i}/{len(faltan)}] fallo de red ({registro['error'][:60]}), sigue")
            time.sleep(2)
            continue

        cache[rc] = registro
        anexar_cache(registro)
        if registro["lat"] is not None:
            aciertos += 1

        if i % 500 == 0 or i == len(faltan):
            print(f"  [{i}/{len(faltan)}] {aciertos:,} resueltas ({100 * aciertos / i:.1f}%)")

        time.sleep(delay)

    if fallos_red:
        print(f"\n  Fallos de red no cacheados: {fallos_red} (se reintentan al relanzar)")
    return cache


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def cargar_valencia() -> pd.DataFrame:
    """Une el normalizado (esquema común) con la referencia catastral del crudo."""
    if not ENTRADA_RAW.exists() or not ENTRADA_NORM.exists():
        raise SystemExit(
            f"Faltan datos de Valencia. Ejecuta antes:\n"
            f"  python etl/extract/extract_vut_oficial.py --fuentes valencia"
        )

    crudo = pd.read_csv(ENTRADA_RAW, sep=";", low_memory=False)
    norm = pd.read_csv(ENTRADA_NORM, low_memory=False)

    rc = crudo[["signatura", "ref_catastral"]].rename(columns={"signatura": "id_fuente"})
    df = norm.merge(rc.drop_duplicates("id_fuente"), on="id_fuente", how="left")

    df["rc14"] = df["ref_catastral"].astype(str).str.strip().str.upper().str[:14]
    df.loc[df["rc14"].str.len() != 14, "rc14"] = pd.NA
    return df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Geocodifica las VUT de Valencia por referencia catastral."
    )
    parser.add_argument("--muestra", type=int,
                        help="Resuelve sólo N parcelas nuevas (validación previa).")
    parser.add_argument("--delay", type=float, default=DELAY_SEGUNDOS,
                        help=f"Segundos entre peticiones (por defecto {DELAY_SEGUNDOS}).")
    parser.add_argument("--solo-resumen", action="store_true",
                        help="No consulta: recompone la salida desde la caché.")
    args = parser.parse_args()

    df = cargar_valencia()
    print(f"Comunitat Valenciana: {len(df):,} registros")
    print(f"  Con referencia catastral válida: {df['rc14'].notna().sum():,}")
    print(f"  Sin referencia:                  {df['rc14'].isna().sum():,}")

    parcelas = df["rc14"].dropna().unique().tolist()
    if args.muestra:
        cache = cargar_cache()
        nuevas = [rc for rc in parcelas if rc not in cache]
        parcelas = list(cache) + nuevas[: args.muestra]
        print(f"\nModo muestra: {args.muestra} parcelas nuevas")

    inicio = time.time()
    cache = cargar_cache() if args.solo_resumen else resolver(parcelas, args.delay)

    # Volcado a las columnas de salida
    res = df["rc14"].map(lambda rc: cache.get(rc) if pd.notna(rc) else None)
    df["lat_catastro"] = [r["lat"] if r else None for r in res]
    df["lon_catastro"] = [r["lon"] if r else None for r in res]
    df["catastro_ldt"] = [r.get("ldt") if r else None for r in res]
    df["catastro_error"] = [r.get("error") if r else None for r in res]

    # Descarte de coordenadas imposibles antes de darlas por buenas.
    fuera = (
        df["lat_catastro"].notna()
        & ~(df["lat_catastro"].between(CAJA_CV[0], CAJA_CV[1])
            & df["lon_catastro"].between(CAJA_CV[2], CAJA_CV[3]))
    )
    if fuera.any():
        df.loc[fuera, ["lat_catastro", "lon_catastro"]] = pd.NA
        df.loc[fuera, "catastro_error"] = "coordenada fuera de la Comunitat Valenciana"

    resueltas = df["lat_catastro"].notna()
    df.loc[resueltas, "lat"] = df.loc[resueltas, "lat_catastro"]
    df.loc[resueltas, "lon"] = df.loc[resueltas, "lon_catastro"]
    df["necesita_geocodificacion"] = df["lat"].isna()
    df["geocoding_fuente"] = pd.NA
    df.loc[resueltas, "geocoding_fuente"] = "catastro-ovc"
    df["geocoding_confianza"] = pd.NA
    df.loc[resueltas, "geocoding_confianza"] = "alta"  # parcela exacta, no interpretación

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["ref_catastral"]).to_csv(SALIDA, index=False, encoding="utf-8")

    # ---------------- Resumen ----------------
    total = len(df)
    con_rc = int(df["rc14"].notna().sum())
    print("\n" + "=" * 72)
    print("RESUMEN — CATASTRO / COMUNITAT VALENCIANA")
    print("=" * 72)
    print(f"  Registros:                {total:>8,}")
    print(f"  Con ref. catastral:       {con_rc:>8,}  ({100 * con_rc / total:.1f} %)")
    print(f"  Geocodificados:           {int(resueltas.sum()):>8,}  "
          f"({100 * resueltas.sum() / total:.1f} % del total)")
    if con_rc:
        print(f"    sobre los que tienen RC:{100 * resueltas.sum() / con_rc:>7.1f} %")
    print(f"  Sin resolver:             {int((~resueltas).sum()):>8,}")
    print(f"\n  Duración: {(time.time() - inicio) / 60:.1f} min")

    errores = df.loc[~resueltas & df["catastro_error"].notna(), "catastro_error"]
    if len(errores):
        print("\n  Motivos de fallo:")
        for motivo, n in errores.value_counts().head(6).items():
            print(f"    {n:>7,}  {motivo[:64]}")

    # Contraste del domicilio devuelto por el Catastro con el municipio del registro.
    muestra = df[resueltas & df["catastro_ldt"].notna()].head(3000)
    if len(muestra):
        coincide = [
            bool(nucleo_municipio(m)) and nucleo_municipio(m) in nucleo_municipio(l)
            for m, l in zip(muestra["municipio"], muestra["catastro_ldt"])
        ]
        print(f"\n  Contraste de municipio (muestra de {len(muestra):,}): "
              f"{100 * sum(coincide) / len(coincide):.1f} % coincide con el del Catastro")

    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    print(f"  Caché:  {CACHE_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
