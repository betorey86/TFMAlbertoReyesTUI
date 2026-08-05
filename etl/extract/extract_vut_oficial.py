"""
Extracción de registros oficiales autonómicos de Viviendas de Uso Turístico (VUT).

Segunda fuente del proyecto, complementaria a OpenStreetMap. OSM infrarrepresenta el
alojamiento no hotelero (en nuestra extracción: 59 % hotel frente a 11 % apartment), así
que el denominador de "oferta" para los indicadores de saturación tiene que venir de los
registros oficiales.

Cada CCAA es una función independiente (`fuente_*`) para poder ir añadiendo comunidades sin
tocar el resto. Todas devuelven un `Resultado` y comparten el mismo esquema normalizado.

Salidas por fuente:
    data/raw/vut_oficial_<slug>.<csv|json>        crudo, tal cual lo sirve el organismo
    data/processed/vut_normalizado_<slug>.csv     esquema común

Uso:
    python etl/extract/extract_vut_oficial.py
    python etl/extract/extract_vut_oficial.py --fuentes canarias barcelona
    python etl/extract/extract_vut_oficial.py --listar
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

UA = {"User-Agent": "TFM-TUI-Dashboard/0.1 (uso academico; extraccion de datos abiertos)"}
TIMEOUT = 900  # algunas descargas oficiales son de cientos de MB

# Esquema normalizado común a todas las fuentes.
COLUMNAS = [
    "id_fuente",
    "nombre",
    "lat",
    "lon",
    "direccion",
    "ccaa",
    "provincia",
    "municipio",
    "plazas",
    "fecha_registro",
    "fuente",
    "necesita_geocodificacion",
]


@dataclass
class Resultado:
    """Desenlace de una fuente, para el resumen final."""
    slug: str
    ccaa: str
    ambito: str
    estado: str                      # "ok" | "pendiente" | "error"
    registros: int = 0
    con_coordenadas: int = 0
    fichero_raw: Path | None = None
    fichero_norm: Path | None = None
    nota: str = ""
    campos_origen: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilidades comunes
# ---------------------------------------------------------------------------

def descargar(url: str, timeout: int = TIMEOUT, reintentos: int = 3) -> requests.Response:
    """
    Descarga con reintentos. Las descargas grandes (OpenRTA son ~325 MB) se cortan a
    media transferencia con relativa frecuencia y un único intento no basta.
    """
    ultimo: Exception | None = None
    for intento in range(1, reintentos + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            ultimo = exc
            if intento < reintentos:
                espera = 15 * intento
                print(f"     Descarga fallida ({type(exc).__name__}). "
                      f"Reintento {intento + 1}/{reintentos} en {espera}s...")
                time.sleep(espera)
    raise RuntimeError(f"No se pudo descargar {url} tras {reintentos} intentos: {ultimo}")


def a_numero(serie: pd.Series | None) -> pd.Series:
    """
    Convierte a float admitiendo coma decimal.

    OpenRTA mezcla los dos formatos en la misma columna ('539165.3196' y '345032,81'),
    así que hay que normalizar el separador antes de convertir.
    """
    if serie is None:
        return pd.Series(dtype="float64")
    if serie.dtype == object:
        serie = serie.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(serie, errors="coerce")


def utm_a_wgs84(x: pd.Series, y: pd.Series, epsg_origen: int = 25830) -> tuple[pd.Series, pd.Series]:
    """Reproyecta coordenadas UTM (por defecto ETRS89 / UTM 30N) a lat/lon WGS84."""
    from pyproj import Transformer

    transformer = Transformer.from_crs(f"EPSG:{epsg_origen}", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(x.to_numpy(), y.to_numpy())
    return pd.Series(lat, index=x.index), pd.Series(lon, index=x.index)


def guardar(df: pd.DataFrame, res: Resultado) -> None:
    """Escribe el normalizado y completa los contadores del resultado."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Coordenadas fuera del rango de España (peninsular + archipiélagos) se descartan.
    # Dos patrones habituales en estos registros:
    #   - (0, 0): ausencia de coordenada codificada como cero, no un punto en el golfo de Guinea.
    #   - valores tipo 28688.0: separador decimal perdido al exportar (28.688).
    # Los segundos son recuperables en la fase de transformación; aquí sólo se anulan.
    nulos = (df["lat"] == 0) & (df["lon"] == 0)
    fuera = df["lat"].notna() & ~df["lat"].between(27.0, 44.0)
    fuera |= df["lon"].notna() & ~df["lon"].between(-19.0, 5.0)
    otros = fuera & ~nulos

    if fuera.any():
        df.loc[fuera, ["lat", "lon"]] = pd.NA
        detalle = []
        if nulos.any():
            detalle.append(f"{int(nulos.sum())} con (0,0)")
        if otros.any():
            detalle.append(f"{int(otros.sum())} fuera del rango de España")
        res.nota = (res.nota + f" Coordenadas anuladas: {', '.join(detalle)}.").strip()

    df["necesita_geocodificacion"] = df["lat"].isna() | df["lon"].isna()

    df = df.reindex(columns=COLUMNAS)
    destino = PROCESSED_DIR / f"vut_normalizado_{res.slug}.csv"
    df.to_csv(destino, index=False, encoding="utf-8")

    res.fichero_norm = destino
    res.registros = len(df)
    res.con_coordenadas = int((~df["necesita_geocodificacion"]).sum())


def guardar_raw_bytes(contenido: bytes, slug: str, ext: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    destino = RAW_DIR / f"vut_oficial_{slug}.{ext}"
    destino.write_bytes(contenido)
    return destino


# ---------------------------------------------------------------------------
# 1. Andalucía — OpenRTA (Registro de Turismo de Andalucía)
# ---------------------------------------------------------------------------

URL_ANDALUCIA = "https://datos.juntadeandalucia.es/api/v0/openrta/all?format=json"

TIPOS_VUT_ANDALUCIA = {
    "Vivienda de uso turístico",
    "Vivienda turística de alojamiento rural",
}


def fuente_andalucia() -> Resultado:
    """
    OpenRTA expone todo el registro turístico en un único JSON, sin autenticación.

    Dos avisos sobre esta fuente:
      - El volcado completo pesa ~325 MB e incluye todas las tipologías (hoteles, guías,
        restauración...). Hay que filtrar en local: el parámetro `object_type` del endpoint
        /search se acepta pero no filtra (devuelve igualmente el total del registro).
      - La variante `format=csv` está mal formada (hay pipes sin escapar dentro de campos
        de texto libre, filas con 92 campos donde la cabecera declara 72), así que se usa
        el JSON aunque sea más pesado.

    Requiere del orden de 4 GB de RAM libres para el parseo.
    """
    res = Resultado("andalucia", "Andalucía", "CCAA completa", "error",
                    nota="OpenRTA (Junta de Andalucía), API pública sin autenticación.")

    r = descargar(URL_ANDALUCIA)
    datos = r.json()

    vut = [d for d in datos if d.get("objects_type_id") in TIPOS_VUT_ANDALUCIA]
    del datos

    # El crudo que se guarda es el subconjunto VUT con sus campos intactos: el volcado
    # entero son 325 MB de los que el 96 % no es alojamiento residencial turístico.
    raw = guardar_raw_bytes(
        json.dumps(vut, ensure_ascii=False).encode("utf-8"), res.slug, "json"
    )
    res.fichero_raw = raw

    df = pd.DataFrame(vut)
    res.campos_origen = list(df.columns)

    x = a_numero(df.get("coord_x"))
    y = a_numero(df.get("coord_y"))
    lat = pd.Series(float("nan"), index=df.index, dtype="float64")
    lon = pd.Series(float("nan"), index=df.index, dtype="float64")
    validas = x.notna() & y.notna()
    if validas.any():
        # srid=25830 (ETRS89 / UTM 30N) en todos los registros que traen coordenadas.
        lat_v, lon_v = utm_a_wgs84(x[validas], y[validas], 25830)
        lat.loc[validas] = lat_v
        lon.loc[validas] = lon_v

    norm = pd.DataFrame({
        "id_fuente": df.get("registration_code", df.get("id")),
        "nombre": df.get("name"),
        "lat": lat,
        "lon": lon,
        "direccion": df.get("establishment_address"),
        "ccaa": "Andalucía",
        "provincia": df.get("provinces"),
        "municipio": df.get("municipalities"),
        "plazas": pd.to_numeric(df.get("tot_gen_places"), errors="coerce"),
        "fecha_registro": df.get("registration_date"),
        "fuente": "OpenRTA - Junta de Andalucía",
    })

    guardar(norm, res)
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# 2. Canarias — Registro General Turístico
# ---------------------------------------------------------------------------

URL_CANARIAS = (
    "https://datos.canarias.es/catalogos/general/dataset/"
    "9f4355a2-d086-4384-ba72-d8c99aa2d544/resource/8ff8cc43-c00b-4513-8f42-a5b961c579e1/"
    "download/establecimientos-extrahoteleros-de-tipologia-vivienda-vacacional-inscritos-"
    "en-el-registro-genera.csv"
)


def fuente_canarias() -> Resultado:
    """CSV directo, ya filtrado a vivienda vacacional y con lat/lon en WGS84."""
    res = Resultado("canarias", "Canarias", "CCAA completa", "error",
                    nota="Registro General Turístico de Canarias, CSV directo.")

    r = descargar(URL_CANARIAS)
    res.fichero_raw = guardar_raw_bytes(r.content, res.slug, "csv")

    df = pd.read_csv(io.BytesIO(r.content), sep=";", low_memory=False)
    res.campos_origen = list(df.columns)

    norm = pd.DataFrame({
        "id_fuente": df.get("establecimiento_id"),
        "nombre": df.get("establecimiento_nombre_comercial").str.strip(),
        "lat": pd.to_numeric(df.get("latitud"), errors="coerce"),
        "lon": pd.to_numeric(df.get("longitud"), errors="coerce"),
        "direccion": df.get("direccion"),
        "ccaa": "Canarias",
        "provincia": df.get("direccion_provincia_nombre"),
        "municipio": df.get("direccion_municipio_nombre"),
        "plazas": pd.to_numeric(df.get("plazas"), errors="coerce"),
        "fecha_registro": pd.NA,  # el CSV no publica fecha de inscripción
        "fuente": "Registro General Turístico de Canarias",
    })

    guardar(norm, res)
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# 3. Illes Balears / Mallorca — Consell de Mallorca
# ---------------------------------------------------------------------------

URL_BALEARES = (
    "https://intranet.caib.es/opendatacataleg/files/dataset/"
    "habitatges_turistics_mallorca/habitatges_turistics_mallorca.csv"
)

# El fichero mezcla el registro de alojamientos con el de comercializadores de estancias
# (agencias), que no son inmuebles y contaminarían el recuento de oferta.
SUBGRUPS_ALOJAMIENTO_BALEARES = {"Allotjaments"}
GRUPS_EXCLUIDOS_BALEARES = {"Comercialitzador d´estades", "Comercialitzador d'estades"}


def fuente_baleares() -> Resultado:
    """
    Registro del Consell de Mallorca. Sólo cubre Mallorca: Menorca, Ibiza y Formentera
    dependen de sus propios consells insulares y publican por separado.
    """
    res = Resultado("baleares", "Illes Balears", "Sólo Mallorca", "error",
                    nota="Consell de Mallorca vía Catàleg de Dades Obertes de les Illes Balears.")

    r = descargar(URL_BALEARES)
    res.fichero_raw = guardar_raw_bytes(r.content, res.slug, "csv")

    df = pd.read_csv(io.BytesIO(r.content), sep=";", encoding="utf-8-sig", low_memory=False)
    res.campos_origen = list(df.columns)

    antes = len(df)
    if "Grup" in df:
        df = df[~df["Grup"].isin(GRUPS_EXCLUIDOS_BALEARES)]
    if "Subgrup" in df:
        df = df[df["Subgrup"].isin(SUBGRUPS_ALOJAMIENTO_BALEARES)]
    excluidos = antes - len(df)
    if excluidos:
        res.nota += f" {excluidos} filas descartadas por no ser alojamiento (comercializadores)."

    norm = pd.DataFrame({
        "id_fuente": df.get("Signatura"),
        "nombre": df.get("Denominació comercial"),
        "lat": pd.to_numeric(df.get("latitude"), errors="coerce"),
        "lon": pd.to_numeric(df.get("longitude"), errors="coerce"),
        "direccion": df.get("Direcció"),
        "ccaa": "Illes Balears",
        "provincia": "Illes Balears",
        "municipio": df.get("Municipi"),
        "plazas": pd.to_numeric(df.get("Places"), errors="coerce"),
        "fecha_registro": df.get("Inici d'activitat"),
        "fuente": "Consell de Mallorca - Registre d'Habitatges Turístics",
    })

    guardar(norm, res)
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# 4. Cataluña / Barcelona — Ajuntament de Barcelona
# ---------------------------------------------------------------------------

URL_BARCELONA = (
    "https://opendata-ajuntament.barcelona.cat/data/dataset/"
    "c748799e-1079-44b1-9e60-88d936a3fe70/resource/"
    "b32fa7f6-d464-403b-8a02-0292a64883bf/download"
)


def fuente_barcelona() -> Resultado:
    """
    HUT de la ciudad de Barcelona (CSV semanal). Sólo el municipio: el registro del resto
    de Cataluña lo lleva la Generalitat (RTC) y no publica volcado abierto equivalente.
    """
    res = Resultado("barcelona", "Cataluña", "Sólo ciudad de Barcelona", "error",
                    nota="Open Data BCN, CSV actualizado semanalmente.")

    r = descargar(URL_BARCELONA)
    res.fichero_raw = guardar_raw_bytes(r.content, res.slug, "csv")

    df = pd.read_csv(io.BytesIO(r.content), low_memory=False)
    res.campos_origen = list(df.columns)

    # La dirección viene troceada en columnas; se recompone para poder cotejar con OSM.
    partes = [
        df.get("TIPUS_CARRER", pd.Series(dtype=object)).fillna(""),
        df.get("CARRER", pd.Series(dtype=object)).fillna(""),
        df.get("NUM1", pd.Series(dtype=object)).fillna("").astype(str).str.replace(r"\.0$", "", regex=True),
    ]
    direccion = (partes[0].astype(str) + " " + partes[1].astype(str) + " " + partes[2]).str.strip()

    norm = pd.DataFrame({
        "id_fuente": df.get("NUMERO_REGISTRE_GENERALITAT", df.get("N_EXPEDIENT")),
        "nombre": pd.NA,  # el registro no publica denominación comercial
        "lat": pd.to_numeric(df.get("LATITUD_Y"), errors="coerce"),
        "lon": pd.to_numeric(df.get("LONGITUD_X"), errors="coerce"),
        "direccion": direccion,
        "ccaa": "Cataluña",
        "provincia": "Barcelona",
        "municipio": "Barcelona",
        "plazas": pd.to_numeric(df.get("NUMERO_PLACES"), errors="coerce"),
        "fecha_registro": pd.NA,
        "fuente": "Open Data BCN - Habitatges d'ús turístic",
    })

    guardar(norm, res)
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# 5. Madrid (ciudad) — Geoportal del Ayuntamiento
# ---------------------------------------------------------------------------

URL_MADRID = (
    "https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/VIVIENDA/"
    "VIVIENDAS_TURISTICAS/VIVIENDAS_USO_TURISTICO.zip"
)


def fuente_madrid() -> Resultado:
    """
    Shapefile en EPSG:25830 con las VUT que tienen licencia urbanística concedida.

    Ojo con el universo: son licencias municipales concedidas, no el registro turístico
    autonómico. Es un subconjunto pequeño y deliberadamente restrictivo — no comparable
    con el número de anuncios activos en plataformas.
    """
    import geopandas as gpd

    res = Resultado("madrid", "Comunidad de Madrid", "Sólo ciudad de Madrid", "error",
                    nota="Geoportal del Ayuntamiento de Madrid, shapefile de licencias concedidas.")

    r = descargar(URL_MADRID)
    res.fichero_raw = guardar_raw_bytes(r.content, res.slug, "zip")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        shp = next(n for n in z.namelist() if n.lower().endswith(".shp"))

    gdf = gpd.read_file(f"zip://{res.fichero_raw}!{shp}").to_crs("EPSG:4326")
    res.campos_origen = [c for c in gdf.columns if c != "geometry"]

    norm = pd.DataFrame({
        "id_fuente": gdf.get("EXPEDIENTE"),
        "nombre": pd.NA,
        "lat": gdf.geometry.y,
        "lon": gdf.geometry.x,
        "direccion": gdf.get("DIRECCION"),
        "ccaa": "Comunidad de Madrid",
        "provincia": "Madrid",
        "municipio": "Madrid",
        "plazas": pd.NA,
        "fecha_registro": pd.to_datetime(gdf.get("RESOLUCION"), errors="coerce").dt.strftime("%Y-%m-%d"),
        "fuente": "Geoportal Ayuntamiento de Madrid - VUT con licencia",
    })
    # UNIDADES_V es el número de viviendas del expediente, no plazas de alojamiento.
    if "UNIDADES_V" in gdf:
        norm["plazas"] = pd.NA

    guardar(norm, res)
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# 6. País Vasco — REATE / Open Data Euskadi
# ---------------------------------------------------------------------------

URL_EUSKADI = (
    "https://opendata.euskadi.eus/contenidos/ds_recursos_turisticos/"
    "habitaciones_viviendas_turisti/opendata/viviendas.json"
)


def fuente_pais_vasco() -> Resultado:
    """
    Censo de viviendas turísticas extraído del REATE. Sin coordenadas: sólo dirección
    postal, así que todos los registros quedan marcados para geocodificar.
    """
    res = Resultado("pais_vasco", "País Vasco", "CCAA completa", "error",
                    nota="Open Data Euskadi (REATE). Sin coordenadas en origen.")

    r = descargar(URL_EUSKADI)
    res.fichero_raw = guardar_raw_bytes(r.content, res.slug, "json")

    df = pd.DataFrame(r.json())
    res.campos_origen = list(df.columns)

    norm = pd.DataFrame({
        "id_fuente": df.get("Nregistro"),
        "nombre": pd.NA,  # el censo no publica denominación comercial
        "lat": pd.NA,
        "lon": pd.NA,
        "direccion": df.get("Direccion"),
        "ccaa": "País Vasco",
        "provincia": df.get("Provincia"),
        "municipio": df.get("Municipio"),
        "plazas": pd.to_numeric(df.get("Capacidad"), errors="coerce"),
        "fecha_registro": df.get("FechainscripcionREATE"),
        "fuente": "Open Data Euskadi - Censo de viviendas turísticas (REATE)",
    })

    guardar(norm, res)
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# 7. Comunitat Valenciana — NO AUTOMATIZABLE
# ---------------------------------------------------------------------------

URL_VALENCIA = (
    "https://dadesobertes.gva.es/dataset/758f8f8e-c5af-4622-b268-a6c591710a51/"
    "resource/b1bdc28e-9813-422a-ab7a-63c21290493d/download/lista-de-viviendas-turisticas.csv"
)


def fuente_valencia() -> Resultado:
    """
    Registro de viviendas turísticas de la Comunitat Valenciana.

    El fichero está en el portal general de datos abiertos de la GVA
    (dadesobertes.gva.es), no en el de Turisme GVA que enlaza a un formulario web sin
    endpoint descargable. Ver la nota del README sobre ese portal.

    No trae coordenadas, pero sí `ref_catastral`, que permite una geocodificación mucho
    más precisa vía el servicio del Catastro que la que daría Nominatim sobre la dirección.
    """
    res = Resultado("valencia", "Comunitat Valenciana", "CCAA completa", "error",
                    nota="Portal de datos abiertos de la GVA (dadesobertes.gva.es).")

    r = descargar(URL_VALENCIA)
    res.fichero_raw = guardar_raw_bytes(r.content, res.slug, "csv")

    df = pd.read_csv(io.BytesIO(r.content), sep=";", low_memory=False)
    res.campos_origen = list(df.columns)

    norm = pd.DataFrame({
        "id_fuente": df.get("signatura"),
        "nombre": df.get("nombre"),
        "lat": pd.NA,
        "lon": pd.NA,
        "direccion": df.get("direccion"),
        "ccaa": "Comunitat Valenciana",
        "provincia": df.get("provincia"),
        "municipio": df.get("municipio"),
        "plazas": a_numero(df.get("plazas_totales")),
        "fecha_registro": df.get("fecha_alta"),
        "fuente": "Generalitat Valenciana - Registro de viviendas turísticas",
    })

    guardar(norm, res)
    res.nota += " Sin coordenadas en origen; incluye referencia catastral para geocodificar."
    res.estado = "ok"
    return res


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

FUENTES = {
    "andalucia": fuente_andalucia,
    "canarias": fuente_canarias,
    "baleares": fuente_baleares,
    "barcelona": fuente_barcelona,
    "madrid": fuente_madrid,
    "pais_vasco": fuente_pais_vasco,
    "valencia": fuente_valencia,
}


def imprimir_resumen(resultados: list[Resultado], duracion: float) -> None:
    print("\n" + "=" * 96)
    print("RESUMEN — REGISTROS OFICIALES DE VUT")
    print("=" * 96)

    ok = [r for r in resultados if r.estado == "ok"]
    pendientes = [r for r in resultados if r.estado == "pendiente"]
    errores = [r for r in resultados if r.estado == "error"]

    if ok:
        cab = f"\n{'Fuente':<14}{'Ámbito':<28}{'Registros':>11}{'Con coord.':>12}{'% coord.':>10}"
        print(cab)
        print("-" * len(cab.strip("\n")))
        total = con_coord = 0
        for r in sorted(ok, key=lambda r: r.registros, reverse=True):
            pct = 100 * r.con_coordenadas / r.registros if r.registros else 0
            print(f"{r.slug:<14}{r.ambito:<28}{r.registros:>11,}{r.con_coordenadas:>12,}{pct:>9.1f}%")
            total += r.registros
            con_coord += r.con_coordenadas
        print("-" * len(cab.strip("\n")))
        pct = 100 * con_coord / total if total else 0
        print(f"{'TOTAL':<14}{'':<28}{total:>11,}{con_coord:>12,}{pct:>9.1f}%")

    if pendientes:
        print(f"\nPENDIENTES DE GESTIÓN MANUAL ({len(pendientes)}):")
        for r in pendientes:
            print(f"  - {r.ccaa}: {r.nota}")

    if errores:
        print(f"\nCON ERRORES ({len(errores)}):")
        for r in errores:
            print(f"  - {r.ccaa}: {r.nota}")

    print(f"\nFuentes con datos: {len(ok)}/{len(resultados)}")
    print(f"Duración total: {duracion / 60:.1f} min")

    if ok:
        print("\nNotas por fuente:")
        for r in sorted(ok, key=lambda r: r.slug):
            print(f"  {r.slug}: {r.nota}")


def guardar_informe(resultados: list[Resultado], duracion: float) -> Path:
    """
    Deja constancia en JSON de qué se obtuvo, para la memoria del TFM.

    Conserva las fuentes de ejecuciones anteriores que no formen parte de esta: así se
    puede relanzar una sola fuente (`--fuentes andalucia`) sin perder el resto del informe.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    destino = PROCESSED_DIR / "vut_informe_fuentes.json"

    previas: dict[str, dict] = {}
    if destino.exists():
        try:
            anterior = json.loads(destino.read_text(encoding="utf-8"))
            previas = {f["slug"]: f for f in anterior.get("fuentes", [])}
        except (ValueError, KeyError):
            previas = {}

    informe = {
        "fecha_extraccion": datetime.now(timezone.utc).isoformat(),
        "duracion_segundos": round(duracion, 1),
        "fuentes": [
            {
                "slug": r.slug,
                "ccaa": r.ccaa,
                "ambito": r.ambito,
                "estado": r.estado,
                "registros": r.registros,
                "con_coordenadas": r.con_coordenadas,
                "necesita_geocodificacion": r.registros - r.con_coordenadas,
                "fichero_raw": str(r.fichero_raw.relative_to(PROJECT_ROOT)) if r.fichero_raw else None,
                "fichero_normalizado": str(r.fichero_norm.relative_to(PROJECT_ROOT)) if r.fichero_norm else None,
                "campos_origen": r.campos_origen,
                "nota": r.nota,
            }
            for r in resultados
        ],
    }

    ejecutadas = {r.slug for r in resultados}
    conservadas = [f for slug, f in previas.items() if slug not in ejecutadas]
    for f in conservadas:
        f["de_ejecucion_anterior"] = True
    informe["fuentes"].extend(conservadas)
    informe["fuentes"].sort(key=lambda f: f["slug"])

    destino.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae y normaliza registros oficiales autonómicos de VUT."
    )
    parser.add_argument("--fuentes", nargs="+", help=f"Subconjunto: {', '.join(FUENTES)}")
    parser.add_argument("--listar", action="store_true", help="Lista las fuentes y sale.")
    args = parser.parse_args()

    if args.listar:
        print("Fuentes disponibles:")
        for slug in FUENTES:
            print(f"  {slug}")
        return 0

    objetivo = args.fuentes or list(FUENTES)
    desconocidas = [f for f in objetivo if f not in FUENTES]
    if desconocidas:
        print(f"Fuentes desconocidas: {', '.join(desconocidas)}", file=sys.stderr)
        print(f"Disponibles: {', '.join(FUENTES)}", file=sys.stderr)
        return 1

    inicio = time.time()
    resultados: list[Resultado] = []

    for i, slug in enumerate(objetivo, start=1):
        print(f"[{i}/{len(objetivo)}] {slug}...")
        t0 = time.time()
        try:
            res = FUENTES[slug]()
        except Exception as exc:  # una fuente caída no debe tumbar el resto
            res = Resultado(slug, slug, "-", "error", nota=f"{type(exc).__name__}: {exc}")
            print(f"  ERROR: {type(exc).__name__}: {exc}")
        else:
            if res.estado == "ok":
                print(f"  {res.registros:,} registros "
                      f"({res.con_coordenadas:,} con coordenadas) en {time.time() - t0:.0f}s")
            else:
                print(f"  {res.estado.upper()}: {res.nota.splitlines()[0]}")
        resultados.append(res)

    duracion = time.time() - inicio
    imprimir_resumen(resultados, duracion)

    informe = guardar_informe(resultados, duracion)
    print(f"\nInforme: {informe.relative_to(PROJECT_ROOT)}")

    if any(r.estado == "error" for r in resultados):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
