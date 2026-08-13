"""
Carga los datos en Railway: municipios, establecimientos y agregados municipales.

Modelo de dos niveles. La carga es **por capas e idempotente**: cada capa se identifica por
su `fuente_dato`, de modo que recargar una sola (por ejemplo Valencia cuando termine su
geocodificación) no obliga a rehacer las demás.

Orden de trabajo:

  1. municipios              desde data/processed/municipios_ine.geojson
  2. establecimientos        una capa por fichero, con `--capas` para elegir
  3. join espacial           rellena establecimientos.codigo_ine con ST_Contains
  4. agregados_municipales   contadores por municipio; dos rutas de entrada:
                             join espacial, y carga directa del agregado de Galicia

Uso:
    python etl/load/cargar_datos.py --todo
    python etl/load/cargar_datos.py --municipios
    python etl/load/cargar_datos.py --capas vut_valencia          # recarga sólo una
    python etl/load/cargar_datos.py --join-espacial --agregados
    python etl/load/cargar_datos.py --control-calidad             # sólo el informe
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import PROJECT_ROOT, describe_target, get_engine

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

LOTE = 5_000  # filas por INSERT


# ---------------------------------------------------------------------------
# Definición de las capas de establecimientos
# ---------------------------------------------------------------------------

# Cada capa: fichero de origen, tipo del ENUM, y de dónde sale el subtipo.
CAPAS_OSM = {
    "osm_alojamientos": ("alojamientos", "alojamiento", ("tourism",)),
    "osm_restauracion": ("restauracion", "restauracion", ("amenity",)),
    "osm_atracciones": ("atracciones", "atraccion", ("tourism", "historic")),
    "osm_transporte": ("transporte_principales", "transporte",
                       ("aeroway", "railway", "amenity", "public_transport")),
}

# VUT con dato de punto. Galicia NO está: entra por la ruta municipal directa.
CAPAS_VUT = {
    "vut_andalucia": ("vut_normalizado_andalucia.csv", "Andalucía"),
    "vut_canarias": ("vut_normalizado_canarias.csv", "Canarias"),
    "vut_baleares": ("vut_normalizado_baleares.csv", "Illes Balears"),
    "vut_barcelona": ("vut_normalizado_barcelona.csv", "Cataluña"),
    "vut_madrid": ("vut_normalizado_madrid.csv", "Comunidad de Madrid"),
    "vut_pais_vasco": ("vut_pais_vasco_geocodificado.csv", "País Vasco"),
    "vut_valencia": ("vut_valencia_geocodificado.csv", "Comunitat Valenciana"),
}

CAPA_CAMPING = "camping"


def todas_las_capas() -> list[str]:
    return list(CAPAS_OSM) + list(CAPAS_VUT) + [CAPA_CAMPING]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def insertar(engine, tabla: str, filas: list[dict], columnas: list[str]) -> int:
    """INSERT por lotes. Devuelve el número de filas insertadas."""
    if not filas:
        return 0
    cols = ", ".join(columnas)
    params = ", ".join(f":{c}" for c in columnas)
    sql = text(f"INSERT INTO {tabla} ({cols}) VALUES ({params}) ON CONFLICT DO NOTHING")

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(filas), LOTE):
            lote = filas[i:i + LOTE]
            conn.execute(sql, lote)
            total += len(lote)
            print(f"    {total:,}/{len(filas):,}".replace(",", "."), end="\r")
    print(" " * 30, end="\r")
    return total


def limpio(valor):
    """Convierte NaN/NaT de pandas en None, que es lo que espera el driver."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if pd.isna(valor) if not isinstance(valor, (list, dict)) else False:
        return None
    return valor


# ---------------------------------------------------------------------------
# 1. Municipios
# ---------------------------------------------------------------------------

def cargar_municipios(engine) -> None:
    import geopandas as gpd

    ruta = PROCESSED_DIR / "municipios_ine.geojson"
    if not ruta.exists():
        raise SystemExit(f"Falta {ruta}. Ejecuta etl/extract/extract_ine_municipios.py")

    print("\n[municipios] Leyendo geometría…")
    gdf = gpd.read_file(ruta)
    print(f"  {len(gdf):,} municipios".replace(",", "."))

    # La tabla exige MultiPolygon: los municipios de una sola pieza llegan como Polygon.
    from shapely.geometry import MultiPolygon
    gdf["geometry"] = gdf.geometry.apply(
        lambda g: g if g is None or g.geom_type == "MultiPolygon" else MultiPolygon([g])
    )

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE municipios CASCADE"))

    filas = [{
        "codigo_ine": str(r.codigo_ine).zfill(5),
        "nombre": r.nombre_municipio,
        "codigo_provincia": str(r.codigo_provincia).zfill(2),
        "provincia": r.provincia,
        "codigo_ccaa": str(r.codigo_ccaa).zfill(2) if limpio(r.codigo_ccaa) else None,
        "ccaa": r.ccaa,
        "poblacion": int(r.poblacion) if limpio(r.poblacion) is not None else None,
        "superficie_km2": float(r.superficie_km2) if limpio(r.superficie_km2) is not None else None,
        "wkt": r.geometry.wkt,
    } for r in gdf.itertuples(index=False)]

    sql = text("""
        INSERT INTO municipios
            (codigo_ine, nombre, codigo_provincia, provincia, codigo_ccaa, ccaa,
             poblacion, superficie_km2, geometria)
        VALUES
            (:codigo_ine, :nombre, :codigo_provincia, :provincia, :codigo_ccaa, :ccaa,
             :poblacion, :superficie_km2, ST_GeomFromText(:wkt, 4326))
        ON CONFLICT (codigo_ine) DO NOTHING
    """)
    print("  Insertando…")
    with engine.begin() as conn:
        for i in range(0, len(filas), 500):
            conn.execute(sql, filas[i:i + 500])
            print(f"    {min(i + 500, len(filas)):,}/{len(filas):,}".replace(",", "."), end="\r")
    print(" " * 30, end="\r")

    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM municipios")).scalar()
    print(f"  Cargados: {n:,} municipios".replace(",", "."))


# ---------------------------------------------------------------------------
# 2. Establecimientos
# ---------------------------------------------------------------------------

COLUMNAS_EST = [
    "id_fuente", "fuente_dato", "nombre", "tipo", "subtipo",
    "lat", "lon", "ccaa", "provincia", "municipio", "plazas",
]


def _filas_osm(prefijo: str, tipo: str, claves: tuple[str, ...], fuente: str) -> list[dict]:
    import json

    ficheros = [f for f in RAW_DIR.glob(f"osm_{prefijo}_*.json") if "consolidado" not in f.name]
    por_ccaa: dict[str, Path] = {}
    for f in ficheros:
        partes = f.stem.split("_")
        slug = partes[-2] if len(partes) >= 2 else f.stem
        if slug not in por_ccaa or f.name > por_ccaa[slug].name:
            por_ccaa[slug] = f

    filas = []
    for fichero in por_ccaa.values():
        try:
            with fichero.open(encoding="utf-8") as fh:
                datos = json.load(fh)
        except (ValueError, OSError):
            print(f"    aviso: {fichero.name} ilegible, se omite")
            continue

        ccaa = datos.get("metadata", {}).get("ccaa_nombre")
        for el in datos.get("osm", {}).get("elements", []):
            tags = el.get("tags", {})
            lat, lon = el.get("lat"), el.get("lon")
            if lat is None or lon is None:
                centro = el.get("center") or {}
                lat, lon = centro.get("lat"), centro.get("lon")
            if lat is None or lon is None:
                continue

            subtipo = next((tags[c] for c in claves if c in tags), None)
            filas.append({
                "id_fuente": f"{el['type']}/{el['id']}",
                "fuente_dato": fuente,
                "nombre": tags.get("name"),
                "tipo": tipo,
                "subtipo": subtipo,
                "lat": float(lat), "lon": float(lon),
                "ccaa": ccaa, "provincia": None,
                "municipio": tags.get("addr:city"),
                "plazas": None,
            })
    return filas


def _filas_vut(fichero: str, ccaa: str, fuente: str) -> list[dict]:
    ruta = PROCESSED_DIR / fichero
    if not ruta.exists():
        print(f"    aviso: falta {fichero}, capa omitida")
        return []

    df = pd.read_csv(ruta, low_memory=False)
    df = df[df["lat"].notna() & df["lon"].notna()]
    filas = []
    for r in df.itertuples(index=False):
        filas.append({
            "id_fuente": limpio(getattr(r, "id_fuente", None)),
            "fuente_dato": fuente,
            "nombre": limpio(getattr(r, "nombre", None)),
            "tipo": "alojamiento",
            "subtipo": "vivienda_uso_turistico",
            "lat": float(r.lat), "lon": float(r.lon),
            "ccaa": ccaa,
            "provincia": limpio(getattr(r, "provincia", None)),
            "municipio": limpio(getattr(r, "municipio", None)),
            "plazas": int(r.plazas) if limpio(getattr(r, "plazas", None)) is not None else None,
        })
    return filas


def _filas_camping(fuente: str) -> list[dict]:
    filas = []
    for ruta in sorted(PROCESSED_DIR.glob("camping_normalizado_*.csv")):
        df = pd.read_csv(ruta, low_memory=False)
        if df.empty:
            continue
        df = df[df["lat"].notna() & df["lon"].notna()]
        for r in df.itertuples(index=False):
            filas.append({
                "id_fuente": limpio(getattr(r, "id_fuente", None)),
                "fuente_dato": fuente,
                "nombre": limpio(getattr(r, "nombre", None)),
                "tipo": "alojamiento",
                "subtipo": limpio(getattr(r, "tipo", None)),
                "lat": float(r.lat), "lon": float(r.lon),
                "ccaa": limpio(getattr(r, "ccaa", None)),
                "provincia": limpio(getattr(r, "provincia", None)),
                "municipio": limpio(getattr(r, "municipio", None)),
                "plazas": int(r.plazas) if limpio(getattr(r, "plazas", None)) is not None else None,
            })
    return filas


def cargar_capa(engine, capa: str) -> int:
    """Carga una capa. Borra antes lo que hubiera de esa misma `fuente_dato`."""
    fuente = f"tfm:{capa}"
    print(f"\n[{capa}]")

    if capa in CAPAS_OSM:
        prefijo, tipo, claves = CAPAS_OSM[capa]
        filas = _filas_osm(prefijo, tipo, claves, fuente)
    elif capa in CAPAS_VUT:
        fichero, ccaa = CAPAS_VUT[capa]
        filas = _filas_vut(fichero, ccaa, fuente)
    elif capa == CAPA_CAMPING:
        filas = _filas_camping(fuente)
    else:
        raise ValueError(f"Capa desconocida: {capa}")

    if not filas:
        print("  Sin filas que cargar.")
        return 0

    # Recarga limpia de esta capa, sin tocar las demás.
    with engine.begin() as conn:
        borradas = conn.execute(
            text("DELETE FROM establecimientos WHERE fuente_dato = :f"), {"f": fuente}
        ).rowcount
    if borradas:
        print(f"  Eliminadas {borradas:,} filas previas de esta capa".replace(",", "."))

    print(f"  Insertando {len(filas):,} filas…".replace(",", "."))
    n = insertar(engine, "establecimientos", filas, COLUMNAS_EST)
    print(f"  Cargadas: {n:,}".replace(",", "."))
    return n


# ---------------------------------------------------------------------------
# 3. Join espacial
# ---------------------------------------------------------------------------

def join_espacial(engine) -> None:
    """Asigna a cada establecimiento el municipio cuyo polígono lo contiene."""
    print("\n[join espacial] Asignando municipio a cada punto…")
    with engine.begin() as conn:
        conn.execute(text("UPDATE establecimientos SET codigo_ine = NULL"))
        # ST_Contains sobre el índice GIST de municipios.geometria.
        resultado = conn.execute(text("""
            UPDATE establecimientos e
            SET codigo_ine = m.codigo_ine
            FROM municipios m
            WHERE e.geom IS NOT NULL
              AND ST_Contains(m.geometria, e.geom)
        """))
    print(f"  Puntos asignados: {resultado.rowcount:,}".replace(",", "."))


# ---------------------------------------------------------------------------
# 4. Agregados municipales
# ---------------------------------------------------------------------------

# Cobertura del registro de VUT por comunidad, tal como se documenta en el inventario.
COBERTURA_VUT = {
    "Andalucía": "completa", "Canarias": "completa", "Comunitat Valenciana": "completa",
    "País Vasco": "completa", "Galicia": "municipal",
    "Illes Balears": "parcial", "Cataluña": "parcial",
    "Comunidad de Madrid": "no_comparable",
}


def calcular_agregados(engine) -> None:
    print("\n[agregados] Calculando contadores por municipio…")

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE agregados_municipales"))

        # --- Ruta (a): join espacial ---
        conn.execute(text("""
            INSERT INTO agregados_municipales (
                codigo_ine,
                n_alojamientos_osm, n_vut_oficial, plazas_vut_declaradas, n_camping,
                n_restauracion, n_atracciones, n_transporte,
                origen_osm, origen_vut, pct_vut_con_plazas
            )
            SELECT
                m.codigo_ine,
                COUNT(*) FILTER (WHERE e.fuente_dato = 'tfm:osm_alojamientos'),
                COUNT(*) FILTER (WHERE e.subtipo = 'vivienda_uso_turistico'),
                SUM(e.plazas) FILTER (WHERE e.subtipo = 'vivienda_uso_turistico'),
                COUNT(*) FILTER (WHERE e.fuente_dato = 'tfm:camping'),
                COUNT(*) FILTER (WHERE e.fuente_dato = 'tfm:osm_restauracion'),
                COUNT(*) FILTER (WHERE e.fuente_dato = 'tfm:osm_atracciones'),
                COUNT(*) FILTER (WHERE e.fuente_dato = 'tfm:osm_transporte'),
                'join_espacial'::origen_agregacion,
                CASE WHEN COUNT(*) FILTER (WHERE e.subtipo = 'vivienda_uso_turistico') > 0
                     THEN 'join_espacial'::origen_agregacion
                     ELSE 'sin_dato'::origen_agregacion END,
                CASE WHEN COUNT(*) FILTER (WHERE e.subtipo = 'vivienda_uso_turistico') > 0
                     THEN ROUND(100.0
                          * COUNT(*) FILTER (WHERE e.subtipo = 'vivienda_uso_turistico'
                                               AND e.plazas IS NOT NULL)
                          / COUNT(*) FILTER (WHERE e.subtipo = 'vivienda_uso_turistico'), 2)
                END
            FROM municipios m
            LEFT JOIN establecimientos e ON e.codigo_ine = m.codigo_ine
            GROUP BY m.codigo_ine
        """))

        # Cobertura declarada, por comunidad.
        for ccaa, cobertura in COBERTURA_VUT.items():
            conn.execute(text("""
                UPDATE agregados_municipales a
                SET cobertura_vut = :cob
                FROM municipios m
                WHERE a.codigo_ine = m.codigo_ine AND m.ccaa = :ccaa
            """), {"cob": cobertura, "ccaa": ccaa})

        conn.execute(text("""
            UPDATE agregados_municipales
            SET cobertura_vut = 'sin_registro'
            WHERE cobertura_vut IS NULL
        """))

    # --- Ruta (b): carga directa del agregado de Galicia ---
    ruta = PROCESSED_DIR / "vut_galicia_municipal.csv"
    if not ruta.exists():
        print("  AVISO: falta vut_galicia_municipal.csv; Galicia queda sin agregado de VUT.")
        return

    gal = pd.read_csv(ruta, dtype={"codigo_ine": str})
    gal = gal[gal["codigo_ine"].notna()]
    filas = [{
        "codigo_ine": str(r.codigo_ine).zfill(5),
        "n_vut": int(r.n_vut),
        "plazas": int(r.plazas_total) if limpio(r.plazas_total) is not None else None,
        "pct": float(r.pct_con_plazas) if limpio(r.pct_con_plazas) is not None else None,
    } for r in gal.itertuples(index=False)]

    print(f"  Galicia por carga directa: {len(filas):,} concellos".replace(",", "."))
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE agregados_municipales
            SET n_vut_oficial = :n_vut,
                plazas_vut_declaradas = :plazas,
                pct_vut_con_plazas = :pct,
                origen_vut = 'municipal_directo'::origen_agregacion,
                fuente_vut = 'REAT - Xunta de Galicia',
                cobertura_vut = 'municipal'
            WHERE codigo_ine = :codigo_ine
        """), filas)

    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM agregados_municipales")).scalar()
    print(f"  Agregados: {n:,} municipios".replace(",", "."))


# ---------------------------------------------------------------------------
# 5. Control de calidad
# ---------------------------------------------------------------------------

def control_calidad(engine) -> None:
    print("\n" + "=" * 78)
    print("CONTROL DE CALIDAD DEL JOIN ESPACIAL")
    print("=" * 78)

    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM establecimientos")).scalar()
        con_muni = conn.execute(
            text("SELECT COUNT(*) FROM establecimientos WHERE codigo_ine IS NOT NULL")
        ).scalar()
        huerfanos = total - con_muni
        print(f"\n  Establecimientos:  {total:>9,}".replace(",", "."))
        print(f"  Con municipio:     {con_muni:>9,}  ({100 * con_muni / total:.2f} %)".replace(",", "."))
        print(f"  Huérfanos:         {huerfanos:>9,}  ({100 * huerfanos / total:.2f} %)".replace(",", "."))

        print("\n  Huérfanos por capa:")
        for fila in conn.execute(text("""
            SELECT fuente_dato,
                   COUNT(*) FILTER (WHERE codigo_ine IS NULL) AS huerfanos,
                   COUNT(*) AS total
            FROM establecimientos GROUP BY fuente_dato ORDER BY 2 DESC
        """)):
            pct = 100 * fila.huerfanos / fila.total if fila.total else 0
            print(f"    {fila.fuente_dato:<28} {fila.huerfanos:>7,} / {fila.total:>8,}"
                  .replace(",", ".") + f"  ({pct:.2f} %)")

        if huerfanos:
            print("\n  Ejemplos de huérfanos (para diagnóstico):")
            for fila in conn.execute(text("""
                SELECT nombre, fuente_dato, lat, lon, ccaa, municipio
                FROM establecimientos
                WHERE codigo_ine IS NULL AND geom IS NOT NULL
                ORDER BY random() LIMIT 12
            """)):
                nombre = (fila.nombre or "(sin nombre)")[:28]
                print(f"    {nombre:<28} {fila.lat:>9.4f},{fila.lon:>10.4f}  "
                      f"{(fila.ccaa or '?')[:18]:<18} {fila.fuente_dato}")

            print("\n  Diagnóstico automático de los huérfanos:")
            d = conn.execute(text("""
                SELECT
                  COUNT(*) FILTER (WHERE lat NOT BETWEEN 27 AND 44
                                      OR lon NOT BETWEEN -19 AND 5)  AS fuera_espana,
                  COUNT(*) FILTER (WHERE lat BETWEEN -19 AND 5
                                     AND lon BETWEEN 27 AND 44)      AS invertidas,
                  COUNT(*) FILTER (WHERE lat BETWEEN 27 AND 44
                                     AND lon BETWEEN -19 AND 5)      AS dentro_caja
                FROM establecimientos WHERE codigo_ine IS NULL AND geom IS NOT NULL
            """)).one()
            print(f"    Fuera del rango de España:     {d.fuera_espana:>7,}".replace(",", "."))
            print(f"    Lat/lon posiblemente invertidas:{d.invertidas:>7,}".replace(",", "."))
            print(f"    Dentro del rango (costa/mar):  {d.dentro_caja:>7,}".replace(",", "."))

            dist = conn.execute(text("""
                SELECT ROUND(AVG(d)::numeric, 1) AS media,
                       ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY d))::numeric, 1) AS mediana,
                       COUNT(*) FILTER (WHERE d < 1000) AS a_menos_1km
                FROM (
                    SELECT MIN(ST_Distance(e.geom::geography, m.geometria::geography)) AS d
                    FROM establecimientos e
                    JOIN municipios m ON ST_DWithin(e.geom::geography, m.geometria::geography, 5000)
                    WHERE e.codigo_ine IS NULL AND e.geom IS NOT NULL
                    GROUP BY e.id
                ) s
            """)).one()
            if dist.media is not None:
                print(f"\n    Distancia al municipio más cercano (los que están a <5 km):")
                print(f"      mediana {dist.mediana} m, media {dist.media} m, "
                      f"{dist.a_menos_1km:,} a menos de 1 km".replace(",", "."))
                print("      Distancias pequeñas indican imprecisión del polígono en costa,")
                print("      no un error de coordenada.")

        # --- Validación de sentido ---
        print("\n" + "-" * 78)
        print("  TOP 10 MUNICIPIOS POR Nº DE VUT")
        print("-" * 78)
        for f in conn.execute(text("""
            SELECT m.nombre, m.provincia, m.poblacion,
                   a.n_vut_oficial, a.plazas_vut_declaradas, a.origen_vut
            FROM agregados_municipales a JOIN municipios m USING (codigo_ine)
            ORDER BY a.n_vut_oficial DESC LIMIT 10
        """)):
            plazas = f"{f.plazas_vut_declaradas:,}".replace(",", ".") if f.plazas_vut_declaradas else "s/d"
            print(f"    {f.nombre[:24]:<24} {f.provincia[:14]:<14} "
                  f"{f.n_vut_oficial:>7,} VUT  {plazas:>9} plazas  {f.origen_vut}".replace(",", "."))

        print("\n" + "-" * 78)
        print("  TOP 10 POR PLAZAS DE VUT POR 1.000 HABITANTES (municipios de +1.000 hab)")
        print("-" * 78)
        for f in conn.execute(text("""
            SELECT m.nombre, m.provincia, m.poblacion,
                   a.plazas_vut_declaradas,
                   ROUND(1000.0 * a.plazas_vut_declaradas / m.poblacion, 1) AS ratio
            FROM agregados_municipales a JOIN municipios m USING (codigo_ine)
            WHERE m.poblacion > 1000 AND a.plazas_vut_declaradas > 0
            ORDER BY ratio DESC LIMIT 10
        """)):
            print(f"    {f.nombre[:24]:<24} {f.provincia[:14]:<14} "
                  f"{f.poblacion:>8,} hab  {f.plazas_vut_declaradas:>8,} plazas  "
                  f"{f.ratio:>8} por 1.000 hab".replace(",", "."))


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Carga los datos del TFM en Railway.")
    parser.add_argument("--todo", action="store_true", help="Todos los pasos, en orden.")
    parser.add_argument("--municipios", action="store_true")
    parser.add_argument("--capas", nargs="*", help=f"Capas: {', '.join(todas_las_capas())}")
    parser.add_argument("--join-espacial", action="store_true")
    parser.add_argument("--agregados", action="store_true")
    parser.add_argument("--control-calidad", action="store_true")
    args = parser.parse_args()

    if not any([args.todo, args.municipios, args.capas is not None,
                args.join_espacial, args.agregados, args.control_calidad]):
        parser.error("indica al menos un paso (o --todo)")

    try:
        print(f"Destino: {describe_target()}")
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"\nERROR de conexión: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("\nRevisa DATABASE_URL en .env (URL PÚBLICA de Railway) y que el esquema "
              "esté aplicado con etl/load/init_db.py.", file=sys.stderr)
        return 2

    if args.todo or args.municipios:
        cargar_municipios(engine)

    if args.todo or args.capas is not None:
        capas = args.capas if args.capas else todas_las_capas()
        desconocidas = [c for c in capas if c not in todas_las_capas()]
        if desconocidas:
            print(f"Capas desconocidas: {desconocidas}", file=sys.stderr)
            return 1
        for capa in capas:
            cargar_capa(engine, capa)

    if args.todo or args.join_espacial:
        join_espacial(engine)

    if args.todo or args.agregados:
        calcular_agregados(engine)

    if args.todo or args.control_calidad:
        control_calidad(engine)

    print("\nHecho.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
