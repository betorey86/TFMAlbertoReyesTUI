"""
Descarga la base municipal de España: geometría, población y superficie.

Es la unidad territorial de análisis del proyecto. Todo lo demás —oferta de alojamiento,
VUT, restauración, atracciones, transporte, camping— se agregará a este nivel para poder
comparar territorios en igualdad, incluida Galicia, que sólo tiene resolución municipal.

Fuentes, ambas oficiales y descargables sin autenticación:

  Geometría   INE, cartografía del seccionado censal
              https://www.ine.es/prodyser/cartografia/seccionado_<AÑO>.zip
              Son secciones censales (36.669), que se disuelven por `CUMUN` para obtener
              el municipio. Se usa el seccionado y no el WFS de unidades administrativas
              del IGN porque este último sólo sirve GML a máxima resolución: 200 provincias
              pesan 81 MB, así que los 8.100 municipios serían inmanejables.

  Población   INE, fichero del padrón por municipio
              https://www.ine.es/pob_xls/pobmun.zip

  Superficie  Calculada sobre el propio polígono, reproyectado a un CRS de área igual
              (EPSG:25830, ETRS89 / UTM 30N). El seccionado no publica superficie.

La clave de cruce es siempre el **código INE de 5 dígitos** (2 de provincia + 3 de
municipio), nunca el nombre: el registro escribe "A CORUÑA" y el INE "Coruña, A", y cruzar
por nombre falla justo en los municipios grandes.

Uso:
    python etl/extract/extract_ine_municipios.py
    python etl/extract/extract_ine_municipios.py --anio 2025
    python etl/extract/extract_ine_municipios.py --sin-geojson   # sólo el CSV
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

UA = {"User-Agent": "TFM-TUI-Dashboard/0.1 (proyecto academico TFM)"}
TIMEOUT = 900

URL_SECCIONADO = "https://www.ine.es/prodyser/cartografia/seccionado_{anio}.zip"
URL_POBLACION = "https://www.ine.es/pob_xls/pobmun.zip"

# CRS proyectado peninsular: se usa para medir superficie en km². Calcularla sobre
# coordenadas geográficas daría un valor sin sentido.
CRS_AREA = "EPSG:25830"
CRS_SALIDA = "EPSG:4326"


def descargar(url: str, destino: Path, descripcion: str) -> Path:
    """Descarga con caché en disco: no se vuelve a bajar lo que ya está."""
    if destino.exists() and destino.stat().st_size > 0:
        print(f"  {descripcion}: ya descargado ({destino.stat().st_size / 1e6:.1f} MB)")
        return destino

    print(f"  {descripcion}: descargando…")
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(r.content)
    print(f"  {descripcion}: {len(r.content) / 1e6:.1f} MB")
    return destino


def normalizar(texto: object) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Geometría
# ---------------------------------------------------------------------------

def cargar_municipios(zip_path: Path) -> gpd.GeoDataFrame:
    """Disuelve las secciones censales en municipios."""
    with zipfile.ZipFile(zip_path) as z:
        shp = next(n for n in z.namelist() if n.lower().endswith(".shp"))

    print("  Leyendo secciones censales…")
    secciones = gpd.read_file(f"zip://{zip_path}!{shp}")
    print(f"  Secciones: {len(secciones):,}")

    if secciones.crs is None:
        secciones = secciones.set_crs(CRS_AREA)

    # Los atributos son constantes dentro de un municipio, así que basta el primero.
    atributos = {
        "NMUN": "first", "CPRO": "first", "NPRO": "first",
        "CCA": "first", "NCA": "first",
    }
    print("  Disolviendo secciones por municipio…")
    municipios = secciones.dissolve(by="CUMUN", aggfunc=atributos, as_index=False)
    print(f"  Municipios: {len(municipios):,}")

    # Superficie en el CRS proyectado, antes de pasar a coordenadas geográficas.
    en_area = municipios.to_crs(CRS_AREA)
    municipios["superficie_km2"] = (en_area.geometry.area / 1e6).round(4)

    municipios = municipios.to_crs(CRS_SALIDA)
    municipios = municipios.rename(columns={
        "CUMUN": "codigo_ine",
        "NMUN": "nombre_municipio",
        "CPRO": "codigo_provincia",
        "NPRO": "provincia",
        "CCA": "codigo_ccaa",
        "NCA": "ccaa",
    })
    municipios["codigo_ine"] = municipios["codigo_ine"].astype(str).str.zfill(5)
    return municipios


# ---------------------------------------------------------------------------
# Población
# ---------------------------------------------------------------------------

def _anio_de_fichero(nombre: str) -> int | None:
    """`pobmun/pobmun24.xlsx` -> 2024. Los sufijos 98 y 99 son de los años noventa."""
    m = re.search(r"pobmun(\d{2})\.(xls|xlsx)$", nombre, re.IGNORECASE)
    if not m:
        return None
    nn = int(m.group(1))
    return 1900 + nn if nn >= 90 else 2000 + nn


def cargar_poblacion(zip_path: Path) -> tuple[pd.DataFrame, int]:
    """
    Lee el padrón municipal del **año más reciente** disponible en el ZIP.

    `pobmun.zip` contiene una edición por año desde 1998. Procesarlas todas y agrupar por
    municipio sumaría 25 padrones: da 607 millones de habitantes en vez de 47. Hay que
    quedarse con un único año, y se elige el más reciente que se pueda leer.

    El código INE de 5 dígitos se reconstruye a partir de CPRO y CMUN, que es como el INE
    publica el desglose.
    """
    with zipfile.ZipFile(zip_path) as z:
        candidatos = [
            (a, n) for n in z.namelist()
            if (a := _anio_de_fichero(n)) is not None
        ]
    if not candidatos:
        raise RuntimeError("El ZIP del padrón no contiene ficheros pobmun<AA>.xls(x).")

    # Los .xls antiguos necesitan xlrd, que puede no estar instalado; se prueban de más
    # reciente a más antiguo y se usa el primero que se deje leer.
    candidatos.sort(reverse=True)

    filas: list[pd.DataFrame] = []
    anio_usado = 0
    with zipfile.ZipFile(zip_path) as z:
        for anio, nombre in candidatos:
            filas = []
            with z.open(nombre) as fh:
                contenido = fh.read()
            try:
                libro = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None)
            except Exception as exc:
                print(f"    {nombre}: no legible ({type(exc).__name__}), se prueba el anterior")
                continue

            for hoja in libro.values():
                # La cabecera real no está en la primera fila: se localiza buscando CPRO.
                fila_cab = None
                for i in range(min(12, len(hoja))):
                    valores = [normalizar(v) for v in hoja.iloc[i].tolist()]
                    if "cpro" in valores and "cmun" in valores:
                        fila_cab = i
                        break
                if fila_cab is None:
                    continue

                df = hoja.iloc[fila_cab + 1:].copy()
                df.columns = [normalizar(c) for c in hoja.iloc[fila_cab].tolist()]
                columnas = {c: c for c in df.columns}
                if "cpro" not in columnas or "cmun" not in columnas:
                    continue

                col_pob = next(
                    (c for c in df.columns if c in ("pob24", "pob25", "pob26", "poblacion", "total")),
                    None,
                )
                if col_pob is None:
                    # Alguna edición nombra la columna como 'pob' + año de dos dígitos.
                    col_pob = next((c for c in df.columns if re.fullmatch(r"pob\d{2}", c or "")), None)
                if col_pob is None:
                    continue

                sub = df[["cpro", "cmun", col_pob]].dropna(subset=["cpro", "cmun"])
                sub.columns = ["cpro", "cmun", "poblacion"]
                filas.append(sub)

            if filas:
                anio_usado = anio
                print(f"    Padrón utilizado: {nombre} (año {anio})")
                break

    if not filas:
        raise RuntimeError("No se pudo extraer la población de ningún fichero del padrón.")

    pob = pd.concat(filas, ignore_index=True)
    pob["codigo_ine"] = (
        pob["cpro"].apply(lambda v: str(int(float(v))).zfill(2))
        + pob["cmun"].apply(lambda v: str(int(float(v))).zfill(3))
    )
    pob["poblacion"] = pd.to_numeric(pob["poblacion"], errors="coerce")
    pob = pob.dropna(subset=["poblacion"])
    # El padrón desglosa por sexo en hojas distintas: aquí se agrupa por municipio dentro
    # de un único año, nunca entre años.
    pob = pob.groupby("codigo_ine", as_index=False)["poblacion"].sum()
    pob["poblacion"] = pob["poblacion"].astype(int)
    return pob, anio_usado


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Descarga geometría, población y superficie de los municipios de España."
    )
    parser.add_argument("--anio", type=int, default=2026,
                        help="Año del seccionado censal del INE (por defecto 2026).")
    parser.add_argument("--sin-geojson", action="store_true",
                        help="No escribe el GeoJSON (pesa bastante); sólo el CSV.")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    inicio = time.time()

    print("[1/4] Descargas")
    try:
        zip_secc = descargar(
            URL_SECCIONADO.format(anio=args.anio),
            RAW_DIR / f"ine_seccionado_{args.anio}.zip",
            "Seccionado censal",
        )
        zip_pob = descargar(URL_POBLACION, RAW_DIR / "ine_pobmun.zip", "Padrón municipal")
    except requests.RequestException as exc:
        print(f"\nERROR de descarga: {exc}", file=sys.stderr)
        return 2

    print("\n[2/4] Geometría municipal")
    municipios = cargar_municipios(zip_secc)

    print("\n[3/4] Población")
    anio_padron = 0
    try:
        poblacion, anio_padron = cargar_poblacion(zip_pob)
        print(f"  Municipios con población: {len(poblacion):,}")
    except RuntimeError as exc:
        print(f"  AVISO: {exc}", file=sys.stderr)
        poblacion = pd.DataFrame(columns=["codigo_ine", "poblacion"])

    print("\n[4/4] Unión y salida")
    # El cruce va por código INE, nunca por nombre.
    municipios = municipios.merge(poblacion, on="codigo_ine", how="left")

    columnas = [
        "codigo_ine", "nombre_municipio", "codigo_provincia", "provincia",
        "codigo_ccaa", "ccaa", "poblacion", "superficie_km2", "geometry",
    ]
    municipios = municipios[columnas].sort_values("codigo_ine")

    csv_path = PROCESSED_DIR / "municipios_ine.csv"
    municipios.drop(columns="geometry").to_csv(csv_path, index=False, encoding="utf-8")

    geojson_path = PROCESSED_DIR / "municipios_ine.geojson"
    if not args.sin_geojson:
        municipios.to_file(geojson_path, driver="GeoJSON")

    # ---------------- Resumen ----------------
    sin_pob = int(municipios["poblacion"].isna().sum())
    print("\n" + "=" * 68)
    print("BASE MUNICIPAL DEL INE")
    print("=" * 68)
    print(f"  Municipios:            {len(municipios):>8,}")
    print(f"  Con población:         {len(municipios) - sin_pob:>8,}")
    if sin_pob:
        print(f"  Sin población:         {sin_pob:>8,}")
    print(f"  Población total:       {int(municipios['poblacion'].sum()):>8,}"
          + (f"   (padrón {anio_padron})" if anio_padron else ""))
    print(f"  Superficie total:      {municipios['superficie_km2'].sum():>8,.0f} km²")
    print(f"  Provincias:            {municipios['provincia'].nunique():>8}")
    print(f"  Comunidades autónomas: {municipios['ccaa'].nunique():>8}")

    print("\n  Ejemplos:")
    ejemplos = ["28079", "08019", "36051", "07040", "35010"]
    muestra = municipios[municipios["codigo_ine"].isin(ejemplos)]
    for _, f in muestra.iterrows():
        pob = f"{int(f['poblacion']):,}".replace(",", ".") if pd.notna(f["poblacion"]) else "s/d"
        print(f"    {f['codigo_ine']}  {f['nombre_municipio']:<24} {f['provincia']:<18} "
              f"{pob:>10} hab  {f['superficie_km2']:>9,.1f} km²")

    print(f"\n  CSV:     {csv_path.relative_to(PROJECT_ROOT)}")
    if not args.sin_geojson:
        print(f"  GeoJSON: {geojson_path.relative_to(PROJECT_ROOT)} "
              f"({geojson_path.stat().st_size / 1e6:.1f} MB)")
    print(f"\n  Duración: {(time.time() - inicio) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
