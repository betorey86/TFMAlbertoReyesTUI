"""
Agregación municipal de todas las capas de oferta.

Produce `data/processed/agregados_municipales.csv`: una fila por cada uno de los 8.132
municipios del INE, con los contadores de cada capa. Es la tabla de comparación entre
territorios, donde Galicia entra en igualdad pese a no tener dato de punto.

Dos rutas de entrada hacia la misma fila:

  join_espacial      se cuentan los puntos contenidos en el polígono municipal.
  municipal_directo  el agregado llega ya calculado del registro oficial (Galicia).

Y un tercer estado que es tan importante como los otros dos:

  sin_dato           esa capa no cubre ese territorio. NO es un cero. Confundirlos haría
                     que Aragón, que no publica registro de VUT, apareciera como territorio
                     sin oferta en lugar de como territorio sin dato.

Regla de reasignación de huérfanos (declarable en la memoria): un punto que no cae dentro
de ningún municipio se asigna al más cercano **si está a menos de 500 m**. Son elementos
costeros o portuarios que quedan unos metros fuera del polígono por la precisión del
seccionado en la línea de costa; su distancia mediana al municipio es de 26 m. Los que
queden más lejos se descartan y se reportan.

Uso:
    python etl/transform/agregar_municipal.py
    python etl/transform/agregar_municipal.py --umbral-huerfanos 500
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

SALIDA = PROCESSED_DIR / "agregados_municipales.csv"
INFORME = PROCESSED_DIR / "agregados_informe.json"

# CRS proyectado para medir distancias en metros.
CRS_METRICO = "EPSG:25830"
UMBRAL_HUERFANOS_M = 500

# --- Capas de OSM: prefijo del fichero y etiquetas de las que sale el subtipo ---
CAPAS_OSM = {
    "alojamientos": ("alojamientos", ("tourism",)),
    "restauracion": ("restauracion", ("amenity",)),
    "atracciones": ("atracciones", ("tourism", "historic")),
    "transporte": ("transporte_principales", ("aeroway", "railway", "amenity", "public_transport")),
}

# --- VUT con dato de punto. Galicia no está: entra por la ruta municipal ---
CAPAS_VUT = {
    "vut_andalucia": "vut_normalizado_andalucia.csv",
    "vut_canarias": "vut_normalizado_canarias.csv",
    "vut_baleares": "vut_normalizado_baleares.csv",
    "vut_barcelona": "vut_normalizado_barcelona.csv",
    "vut_madrid": "vut_normalizado_madrid.csv",
    "vut_pais_vasco": "vut_pais_vasco_geocodificado.csv",
    "vut_valencia": "vut_valencia_geocodificado.csv",
}

# Comunidad que cubre cada fuente, para poder acotar el efecto de sus nombres sin resolver.
CAPAS_VUT_CCAA = {
    "vut_andalucia": "Andalucía",
    "vut_canarias": "Canarias",
    "vut_baleares": "Illes Balears",
    "vut_barcelona": "Cataluña",
    "vut_madrid": "Comunidad de Madrid",
    "vut_pais_vasco": "País Vasco",
    "vut_valencia": "Comunitat Valenciana",
}

# --- Cobertura del registro oficial de VUT ---
# Comunidades con registro completo: todos sus municipios tienen dato.
CCAA_VUT_COMPLETA = {"Andalucía", "Canarias", "Comunitat Valenciana", "País Vasco"}
CCAA_VUT_MUNICIPAL = {"Galicia"}

# Coberturas parciales: sólo estos municipios tienen dato; el resto de su comunidad es
# `sin_dato`, no cero. Madrid además mide licencias urbanísticas, no registro turístico.
MUNICIPIOS_VUT_PARCIAL = {
    "08019": "Cataluña (sólo ciudad de Barcelona)",
    "28079": "Comunidad de Madrid (sólo ciudad, licencias urbanísticas)",
}

# Illes Balears: el registro del Consell cubre Mallorca, no Menorca ni Pitiusas. Se
# clasifica por la posición del municipio, que es un criterio reproducible y declarable.
CAJA_MALLORCA = (39.15, 40.05, 2.25, 3.55)  # lat_min, lat_max, lon_min, lon_max


def _leer_json(fichero: Path) -> dict | None:
    try:
        with fichero.open(encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def cargar_puntos() -> pd.DataFrame:
    """Reúne todas las capas con dato de punto en un único DataFrame."""
    filas = []

    for capa, (prefijo, claves) in CAPAS_OSM.items():
        ficheros = [Path(f) for f in glob.glob(str(RAW_DIR / f"osm_{prefijo}_*.json"))
                    if "consolidado" not in f]
        # Una CCAA puede tener varias fechas: se queda la más reciente.
        por_ccaa: dict[str, Path] = {}
        for f in ficheros:
            slug = f.stem.split("_")[-2]
            if slug not in por_ccaa or f.name > por_ccaa[slug].name:
                por_ccaa[slug] = f

        n = 0
        for fichero in por_ccaa.values():
            datos = _leer_json(fichero)
            if datos is None:
                print(f"    aviso: {fichero.name} ilegible, se omite")
                continue
            for el in datos.get("osm", {}).get("elements", []):
                centro = el.get("center") or {}
                lat = el.get("lat", centro.get("lat"))
                lon = el.get("lon", centro.get("lon"))
                if lat is None or lon is None:
                    continue
                filas.append({"capa": capa, "lat": lat, "lon": lon, "plazas": None})
                n += 1
        print(f"  {capa:<14} {n:>8,}".replace(",", "."))

    for capa, fichero in CAPAS_VUT.items():
        ruta = PROCESSED_DIR / fichero
        if not ruta.exists():
            print(f"  {capa:<14} {'—':>8}  (falta {fichero})")
            continue
        df = pd.read_csv(ruta, usecols=lambda c: c in ("lat", "lon", "plazas"), low_memory=False)
        df = df[df["lat"].notna() & df["lon"].notna()]
        for r in df.itertuples(index=False):
            filas.append({
                "capa": "vut", "lat": r.lat, "lon": r.lon,
                "plazas": getattr(r, "plazas", None),
            })
        print(f"  {capa:<14} {len(df):>8,}".replace(",", "."))

    n_camp = 0
    for ruta in sorted(PROCESSED_DIR.glob("camping_normalizado_*.csv")):
        df = pd.read_csv(ruta, low_memory=False)
        if df.empty:
            continue
        df = df[df["lat"].notna() & df["lon"].notna()]
        for r in df.itertuples(index=False):
            filas.append({"capa": "camping", "lat": r.lat, "lon": r.lon, "plazas": None})
        n_camp += len(df)
    print(f"  {'camping':<14} {n_camp:>8,}".replace(",", "."))

    return pd.DataFrame(filas)


def asignar_municipio(puntos: pd.DataFrame, muni: gpd.GeoDataFrame,
                      umbral_m: float) -> tuple[pd.DataFrame, dict]:
    """
    Join espacial con reasignación acotada de los huérfanos.

    Primero `within` contra el polígono. Los que no caen en ninguno se reasignan al
    municipio más cercano sólo si está por debajo del umbral; el resto se descarta.
    """
    gpts = gpd.GeoDataFrame(
        puntos, geometry=gpd.points_from_xy(puntos["lon"], puntos["lat"]), crs="EPSG:4326"
    )
    unido = gpd.sjoin(
        gpts, muni[["codigo_ine", "geometry"]], how="left", predicate="within"
    )
    # Un punto en un borde compartido puede casar con dos polígonos: se queda el primero.
    unido = unido[~unido.index.duplicated(keep="first")]

    huerfanos = unido["codigo_ine"].isna()
    stats = {
        "puntos": len(unido),
        "dentro": int((~huerfanos).sum()),
        "huerfanos": int(huerfanos.sum()),
    }

    if huerfanos.any():
        pendientes = unido[huerfanos].drop(
            columns=[c for c in unido.columns if c.startswith("index_")] + ["codigo_ine"],
            errors="ignore",
        )
        pendientes = gpd.GeoDataFrame(
            pendientes.drop(columns="geometry"),
            geometry=gpd.points_from_xy(pendientes["lon"], pendientes["lat"]),
            crs="EPSG:4326",
        ).to_crs(CRS_METRICO)

        cercano = gpd.sjoin_nearest(
            pendientes, muni[["codigo_ine", "geometry"]].to_crs(CRS_METRICO),
            how="left", distance_col="dist_m",
        )
        cercano = cercano[~cercano.index.duplicated(keep="first")]

        recuperables = cercano["dist_m"] <= umbral_m
        unido.loc[cercano[recuperables].index, "codigo_ine"] = \
            cercano.loc[recuperables, "codigo_ine"]

        stats["reasignados"] = int(recuperables.sum())
        stats["descartados"] = int((~recuperables).sum())
        stats["dist_mediana_reasignados_m"] = (
            round(float(cercano.loc[recuperables, "dist_m"].median()), 1)
            if recuperables.any() else None
        )
        stats["descartados_por_capa"] = (
            cercano.loc[~recuperables, "capa"].value_counts().to_dict()
        )
        stats["descartados_dist_mediana_m"] = (
            round(float(cercano.loc[~recuperables, "dist_m"].median()), 1)
            if (~recuperables).any() else None
        )

    stats["asignados_total"] = int(unido["codigo_ine"].notna().sum())
    return pd.DataFrame(unido.drop(columns="geometry")), stats


def _normalizar(texto: object) -> str:
    import re
    import unicodedata
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


ARTICULOS = {"a", "o", "as", "os", "el", "la", "los", "las", "lo", "l", "es", "sa"}


def _nucleo(texto: object) -> str:
    """Nombre sin artículos y con las variantes bilingües separadas por '/' resueltas."""
    partes = str(texto).split("/") if texto is not None else [""]
    claves = []
    for p in partes:
        palabras = [w for w in _normalizar(p).split() if w not in ARTICULOS]
        if palabras:
            claves.append(" ".join(palabras))
    return claves[0] if claves else ""


def _todas_las_claves(texto: object) -> list[str]:
    """
    Todas las formas del nombre: cada variante bilingüe, sin artículos.

    Se separa por '/' y por ',' porque las dos fuentes ordenan distinto: el INE escribe
    "Balears, Illes" y el registro balear "Illes Balears"; el INE "Coruña, A" y el gallego
    "A CORUÑA". Además de la forma tal cual, se genera una variante con las palabras
    ordenadas alfabéticamente, que hace equivalentes esos dos órdenes.
    """
    import re as _re
    bruto = str(texto) if texto is not None else ""
    partes = _re.split(r"[/,]", bruto) + [bruto]
    claves = set()
    for p in partes:
        palabras = [w for w in _normalizar(p).split() if w not in ARTICULOS]
        if palabras:
            claves.add(" ".join(palabras))
            claves.add(" ".join(sorted(palabras)))
    return sorted(claves, key=len, reverse=True)


def agregar_vut_declarado(muni: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Agrega las VUT por el municipio que **declara el propio registro**, no por su posición.

    Es la corrección de un error de diseño. Contar por join espacial hace que el recuento
    municipal dependa de que la geocodificación esté completa, y no lo está: cuando el
    Catastro cortó la geocodificación de Valencia, lo hizo en mitad de un recorrido ordenado
    por provincia y municipio, de modo que municipios enteros quedaron con 0 puntos.
    Peñíscola tiene 3.016 VUT registradas y 0 geolocalizadas; Oropesa del Mar, 3.198 y 0.

    Con el recuento por punto, esos municipios aparecían como "oportunidad de inversión"
    cuando son de los más saturados de la costa mediterránea.

    El municipio declarado está en el 100 % de los registros oficiales, así que el agregado
    municipal no debe depender de la geocodificación. La geolocalización sigue siendo
    necesaria, pero para el mapa de detalle, no para comparar territorios.
    """
    # Índice de nombres del INE, con la provincia como desambiguador.
    idx_prov: dict[tuple[str, str], str] = {}
    idx_nombre: dict[str, list[str]] = {}
    for r in muni.itertuples(index=False):
        for clave in _todas_las_claves(r.nombre):
            for clave_prov in _todas_las_claves(r.provincia):
                idx_prov[(clave, clave_prov)] = r.codigo_ine
            idx_nombre.setdefault(clave, []).append(r.codigo_ine)

    filas = []
    stats: dict[str, dict] = {}

    for capa, fichero in CAPAS_VUT.items():
        ruta = PROCESSED_DIR / fichero
        if not ruta.exists():
            continue
        df = pd.read_csv(
            ruta,
            usecols=lambda c: c in ("municipio", "provincia", "plazas", "lat", "lon"),
            low_memory=False,
        )
        if "municipio" not in df.columns:
            continue

        resueltos = sin_resolver = 0
        nombres_fallidos: set[str] = set()
        for r in df.itertuples(index=False):
            codigo = None
            claves_muni = _todas_las_claves(getattr(r, "municipio", None))
            claves_prov = _todas_las_claves(getattr(r, "provincia", None))
            for cm in claves_muni:
                for cp in claves_prov:
                    if (cm, cp) in idx_prov:
                        codigo = idx_prov[(cm, cp)]
                        break
                if codigo:
                    break
            if codigo is None:
                # Sin provincia utilizable: se acepta el nombre si es único en España.
                for cm in claves_muni:
                    candidatos = idx_nombre.get(cm, [])
                    if len(candidatos) == 1:
                        codigo = candidatos[0]
                        break
            if codigo is None:
                sin_resolver += 1
                if getattr(r, "municipio", None) is not None:
                    nombres_fallidos.add(str(r.municipio))
                continue

            resueltos += 1
            filas.append({
                "codigo_ine": codigo,
                "capa": capa,
                "plazas": getattr(r, "plazas", None),
            })

        stats[capa] = {
            "registros": len(df),
            "resueltos": resueltos,
            "sin_resolver": sin_resolver,
            "pct": round(100 * resueltos / len(df), 2) if len(df) else 0,
            "nombres_fallidos": sorted(nombres_fallidos)[:8],
        }
        print(f"  {capa:<16} {resueltos:>7,}/{len(df):>7,} resueltos "
              .replace(",", ".") + f"({stats[capa]['pct']:>5.1f} %)")

    if not filas:
        return pd.DataFrame(columns=["codigo_ine", "n_vut", "plazas_vut", "n_vut_con_plazas"]), stats

    declarado = pd.DataFrame(filas)
    agregado = declarado.groupby("codigo_ine").agg(
        n_vut=("capa", "size"),
        plazas_vut=("plazas", "sum"),
        n_vut_con_plazas=("plazas", "count"),
    ).reset_index()
    return agregado, stats


def clasificar_cobertura_vut(muni: pd.DataFrame) -> pd.Series:
    """Devuelve el `origen_agregacion` del bloque de VUT para cada municipio."""
    origen = pd.Series("sin_dato", index=muni.index, dtype=object)

    origen[muni["ccaa"].isin(CCAA_VUT_COMPLETA)] = "join_espacial"
    origen[muni["ccaa"].isin(CCAA_VUT_MUNICIPAL)] = "municipal_directo"
    origen[muni["codigo_ine"].isin(MUNICIPIOS_VUT_PARCIAL)] = "join_espacial"

    # Baleares: el registro del Consell de Mallorca cubre sólo esa isla.
    la1, la2, lo1, lo2 = CAJA_MALLORCA
    es_mallorca = (
        (muni["ccaa"] == "Illes Balears")
        & muni["lat_centro"].between(la1, la2)
        & muni["lon_centro"].between(lo1, lo2)
    )
    origen[es_mallorca] = "join_espacial"
    return origen


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agrega todas las capas de oferta por municipio del INE."
    )
    parser.add_argument("--umbral-huerfanos", type=float, default=UMBRAL_HUERFANOS_M,
                        help="Metros máximos para reasignar un punto huérfano (500 por defecto).")
    args = parser.parse_args()

    geojson = PROCESSED_DIR / "municipios_ine.geojson"
    if not geojson.exists():
        print(f"ERROR: falta {geojson}", file=sys.stderr)
        print("Ejecuta antes: python etl/extract/extract_ine_municipios.py", file=sys.stderr)
        return 1

    inicio = time.time()
    print("[1/4] Base municipal")
    muni = gpd.read_file(geojson)
    muni["codigo_ine"] = muni["codigo_ine"].astype(str).str.zfill(5)
    print(f"  {len(muni):,} municipios".replace(",", "."))

    print("\n[2/4] Capas de punto")
    puntos = cargar_puntos()
    print(f"  {'TOTAL':<14} {len(puntos):>8,}".replace(",", "."))

    print("\n[3/4] Join espacial")
    asignados, stats = asignar_municipio(puntos, muni, args.umbral_huerfanos)
    print(f"  Dentro del polígono:  {stats['dentro']:>8,}".replace(",", "."))
    print(f"  Huérfanos:            {stats['huerfanos']:>8,}".replace(",", "."))
    if stats.get("reasignados") is not None:
        print(f"    reasignados (<{args.umbral_huerfanos:.0f} m): {stats['reasignados']:>6,}"
              .replace(",", ".") + f"  (mediana {stats['dist_mediana_reasignados_m']} m)")
        print(f"    descartados:          {stats['descartados']:>6,}".replace(",", ".")
              + f"  (mediana {stats['descartados_dist_mediana_m']} m)")
    print(f"  Asignados en total:   {stats['asignados_total']:>8,} "
          f"({100 * stats['asignados_total'] / stats['puntos']:.2f} %)".replace(",", "."))

    print("\n[4/4] Agregación")
    validos = asignados[asignados["codigo_ine"].notna()]

    # Las capas de OSM sí se cuentan por join espacial: no declaran municipio.
    conteos = (
        validos[validos["capa"] != "vut"]
        .pivot_table(index="codigo_ine", columns="capa", aggfunc="size", fill_value=0)
        .rename(columns={
            "alojamientos": "n_alojamientos_osm", "restauracion": "n_restauracion",
            "atracciones": "n_atracciones", "transporte": "n_transporte",
            "camping": "n_camping",
        })
    )

    # Las VUT se cuentan por el municipio declarado en el registro. El join espacial se
    # conserva sólo como control de calidad de la geolocalización.
    vut_puntos = validos[validos["capa"] == "vut"]
    geolocalizadas = vut_puntos.groupby("codigo_ine").size().rename("n_vut_geolocalizadas")

    agg = muni.drop(columns="geometry").rename(columns={"nombre_municipio": "nombre"})
    # Centroide, necesario para clasificar la cobertura balear y para los indicadores.
    centros = muni.to_crs(CRS_METRICO).geometry.centroid.to_crs("EPSG:4326")
    agg["lat_centro"] = centros.y.values
    agg["lon_centro"] = centros.x.values

    agg = agg.merge(conteos, on="codigo_ine", how="left")
    agg = agg.merge(geolocalizadas, on="codigo_ine", how="left")

    print("\n  VUT por municipio declarado en el registro:")
    vut_declarado, stats_nombres = agregar_vut_declarado(
        agg[["codigo_ine", "nombre", "provincia"]]
    )
    agg = agg.merge(
        vut_declarado.rename(columns={"n_vut": "n_vut_oficial"}), on="codigo_ine", how="left"
    )

    for col in ("n_alojamientos_osm", "n_restauracion", "n_atracciones",
                "n_transporte", "n_camping", "n_vut_oficial", "n_vut_con_plazas",
                "n_vut_geolocalizadas"):
        if col not in agg.columns:
            agg[col] = 0
        agg[col] = agg[col].fillna(0).astype(int)
    agg["plazas_vut"] = agg["plazas_vut"].fillna(0)

    # --- Origen de cada bloque ---
    agg["origen_osm"] = "join_espacial"   # OSM cubre todo el territorio
    # El agregado de VUT viene del municipio declarado, así que su origen es directo del
    # registro, no del join espacial.
    agg["origen_vut"] = clasificar_cobertura_vut(agg)
    agg.loc[agg["origen_vut"] == "join_espacial", "origen_vut"] = "municipal_directo"

    # Naturaleza del dato, separada de su origen. Madrid publica licencias urbanísticas
    # concedidas, no inscripciones en el registro turístico: su recuento es correcto pero
    # mide otra magnitud, así que no puede entrar en un ranking de saturación junto al
    # resto sin decir que Madrid no tiene presión de vivienda turística, que es falso.
    agg["cobertura_vut"] = "sin_registro"
    agg.loc[agg["origen_vut"] != "sin_dato", "cobertura_vut"] = "registro"
    agg.loc[agg["ccaa"] == "Comunidad de Madrid", "cobertura_vut"] = "no_comparable"
    agg.loc[(agg["ccaa"] == "Comunidad de Madrid") & (agg["origen_vut"] == "sin_dato"),
            "cobertura_vut"] = "sin_registro"

    # Si una fuente deja registros sin resolver, no sabemos a qué municipio pertenecen: sus
    # municipios con 0 VUT podrían ser en realidad municipios con VUT no asignadas. Un 0
    # falso en saturación se convierte en "oportunidad de inversión" en el índice, que es
    # el peor error posible. Se marcan como sin_dato hasta que la resolución sea completa.
    ccaa_no_fiables = set()
    for capa, s in stats_nombres.items():
        if s["sin_resolver"] > 0:
            ccaa_no_fiables.add(CAPAS_VUT_CCAA.get(capa))
    if ccaa_no_fiables:
        dudosos = (
            agg["ccaa"].isin(ccaa_no_fiables)
            & (agg["n_vut_oficial"].fillna(0) == 0)
            & (agg["origen_vut"] != "sin_dato")
        )
        if dudosos.any():
            print(f"  {int(dudosos.sum())} municipios con 0 VUT en fuentes con nombres sin "
                  f"resolver: se marcan sin_dato en vez de cero")
            agg.loc[dudosos, "origen_vut"] = "sin_dato"

    # --- Capa hotelera (EOH del INE) ---
    #
    # La EOH tiene cobertura nacional pero resolución provincial, salvo en los 132 "puntos
    # turísticos" que el INE monitoriza uno a uno. Ahí, y sólo ahí, hay plazas hoteleras
    # municipales fiables.
    #
    # Deliberadamente NO se prorratea la cifra provincial entre los municipios de la
    # provincia. Repartir las 55.000 plazas de Alicante entre sus 141 municipios metería
    # oferta hotelera en pueblos del interior que no tienen ninguna, y el error viajaría
    # después al indicador de saturación como si fuera dato. Se marca `eoh_provincia`,
    # que el cálculo trata como no disponible.
    ruta_eoh = PROCESSED_DIR / "eoh_hotelera.csv"
    agg["plazas_hoteleras"] = pd.NA
    agg["origen_hotelero"] = "sin_dato"
    if ruta_eoh.exists():
        eoh = pd.read_csv(ruta_eoh, dtype={"codigo": str})
        pt = eoh[(eoh["ambito"] == "punto_turistico") & eoh["codigo"].notna()]
        mapa_plazas = dict(zip(pt["codigo"].str.zfill(5), pt["plazas_hoteleras"]))
        mapa_establec = dict(zip(pt["codigo"].str.zfill(5), pt["n_establecimientos"]))

        es_pt = agg["codigo_ine"].isin(mapa_plazas)
        agg.loc[es_pt, "plazas_hoteleras"] = agg.loc[es_pt, "codigo_ine"].map(mapa_plazas)
        agg["n_establecimientos_hoteleros"] = agg["codigo_ine"].map(mapa_establec)
        agg.loc[es_pt, "origen_hotelero"] = "eoh_punto_turistico"

        # Para el resto, la EOH sí tiene dato, pero sólo de su provincia.
        provincias_eoh = {
            _nucleo(n) for n in eoh.loc[eoh["ambito"] == "provincia", "nombre"]
        }
        tiene_provincia = agg["provincia"].map(lambda p: _nucleo(p) in provincias_eoh)
        agg.loc[~es_pt & tiene_provincia, "origen_hotelero"] = "eoh_provincia"

        print(f"  EOH: {int(es_pt.sum())} municipios con dato hotelero propio "
              f"(punto turístico); {int((~es_pt & tiene_provincia).sum())} sólo con dato "
              f"provincial")
    else:
        agg["n_establecimientos_hoteleros"] = pd.NA
        print("  AVISO: falta eoh_hotelera.csv; sin capa hotelera.")

    # Control de calidad: qué parte de las VUT del municipio está geolocalizada. No afecta
    # al recuento, pero avisa de dónde el mapa de puntos está incompleto.
    agg["pct_vut_geolocalizadas"] = (
        100 * agg["n_vut_geolocalizadas"].astype("Float64")
        / agg["n_vut_oficial"].astype("Float64").replace(0, pd.NA)
    ).round(1)

    # --- Ruta municipal directa: Galicia ---
    ruta_gal = PROCESSED_DIR / "vut_galicia_municipal.csv"
    n_galicia = 0
    if ruta_gal.exists():
        gal = pd.read_csv(ruta_gal, dtype={"codigo_ine": str})
        gal = gal[gal["codigo_ine"].notna()]
        gal["codigo_ine"] = gal["codigo_ine"].str.zfill(5)
        mapa_n = dict(zip(gal["codigo_ine"], gal["n_vut"]))
        mapa_p = dict(zip(gal["codigo_ine"], gal["plazas_total"]))
        mapa_c = dict(zip(gal["codigo_ine"], gal["n_con_plazas"]))

        es_gal = agg["codigo_ine"].isin(mapa_n)
        agg.loc[es_gal, "n_vut_oficial"] = agg.loc[es_gal, "codigo_ine"].map(mapa_n).astype(int)
        agg.loc[es_gal, "plazas_vut"] = agg.loc[es_gal, "codigo_ine"].map(mapa_p)
        agg.loc[es_gal, "n_vut_con_plazas"] = (
            agg.loc[es_gal, "codigo_ine"].map(mapa_c).fillna(0).astype(int)
        )
        n_galicia = int(es_gal.sum())
        print(f"  Galicia por ruta municipal_directo: {n_galicia} concellos")

    # Donde no hay dato de VUT, el contador no debe ser 0 sino ausente: un 0 se leería
    # como "no hay oferta" y aquí significa "no hay registro publicado".
    sin_dato = agg["origen_vut"] == "sin_dato"
    agg.loc[sin_dato, ["n_vut_oficial", "plazas_vut", "n_vut_con_plazas",
                       "n_vut_geolocalizadas", "pct_vut_geolocalizadas"]] = pd.NA

    # Asignar pd.NA convierte la columna a object; se vuelve a numérico (nullable) para
    # que los cálculos posteriores traten el ausente como ausente y no como texto.
    for col in ("n_vut_oficial", "n_vut_con_plazas"):
        agg[col] = pd.to_numeric(agg[col], errors="coerce").astype("Int64")
    agg["plazas_vut"] = pd.to_numeric(agg["plazas_vut"], errors="coerce")

    agg["pct_vut_con_plazas"] = (
        100 * agg["n_vut_con_plazas"].astype("Float64")
        / agg["n_vut_oficial"].astype("Float64").replace(0, pd.NA)
    ).round(2)

    columnas = [
        "codigo_ine", "nombre", "provincia", "ccaa", "poblacion", "superficie_km2",
        "lat_centro", "lon_centro",
        "n_alojamientos_osm", "n_vut_oficial", "plazas_vut", "n_vut_con_plazas",
        "pct_vut_con_plazas", "n_vut_geolocalizadas", "pct_vut_geolocalizadas",
        "plazas_hoteleras", "n_establecimientos_hoteleros", "origen_hotelero",
        "n_restauracion", "n_atracciones", "n_camping",
        "n_transporte", "origen_osm", "origen_vut", "cobertura_vut",
    ]
    agg = agg[columnas].sort_values("codigo_ine")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    agg.to_csv(SALIDA, index=False, encoding="utf-8")

    informe = {
        "municipios": len(agg),
        "join": stats,
        "umbral_huerfanos_m": args.umbral_huerfanos,
        "galicia_municipal_directo": n_galicia,
        "resolucion_nombres_vut": stats_nombres,
        "origen_vut": agg["origen_vut"].value_counts().to_dict(),
        "duracion_s": round(time.time() - inicio, 1),
    }
    INFORME.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- Resumen ----------------
    print("\n" + "=" * 70)
    print("AGREGADOS MUNICIPALES")
    print("=" * 70)
    print(f"  Municipios:            {len(agg):>8,}".replace(",", "."))
    print("\n  Origen del bloque de VUT:")
    for origen, n in agg["origen_vut"].value_counts().items():
        print(f"    {origen:<20} {n:>6,} municipios".replace(",", "."))

    con_vut = agg[agg["origen_vut"] != "sin_dato"]
    print(f"\n  Municipios con dato de VUT:  {len(con_vut):>6,}".replace(",", "."))
    print(f"  VUT contabilizadas:          {int(con_vut['n_vut_oficial'].sum()):>6,}".replace(",", "."))
    print(f"  Plazas contabilizadas:       {int(con_vut['plazas_vut'].sum()):>6,}".replace(",", "."))

    print(f"\n  Salida:  {SALIDA.relative_to(PROJECT_ROOT)}")
    print(f"  Informe: {INFORME.relative_to(PROJECT_ROOT)}")
    print(f"\n  Duración: {time.time() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
