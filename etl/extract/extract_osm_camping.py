"""
Capa de camping y áreas de autocaravana desde OpenStreetMap.

Es oferta de alojamiento, no una categoría aparte: `tourism=camp_site` (campings) y
`tourism=caravan_site` (áreas de autocaravana y caravaning). Se extrae con la misma
mecánica que el resto de capas —selección por ISO 3166-2, rotación entre réplicas de
Overpass, reintentos ante respuesta vacía o con `remark`, `out center` para polígonos— y se
normaliza al esquema común de oferta, de modo que camping y área de autocaravana entran como
dos valores más del campo `tipo`, integrables con los alojamientos ya extraídos.

Salidas:
    data/raw/osm_camping_<ccaa>_<fecha>.json          crudo, formato de las demás capas
    data/processed/camping_normalizado_<ccaa>.csv     esquema común de oferta

Uso:
    python etl/extract/extract_osm_camping.py --ccaa baleares
    python etl/extract/extract_osm_camping.py --listar-ccaa
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_osm import (
    CCAA,
    PROJECT_ROOT,
    construir_query_filtros,
    consultar_overpass,
    guardar_raw,
    resumen_por_clave,
)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

FILTROS = ['["tourism"~"^(camp_site|caravan_site)$"]']

# El valor de OSM se traduce a un nombre estable del proyecto: `tourism` puede cambiar de
# vocabulario, y el dashboard no debería depender de ello.
TIPOS = {
    "camp_site": "camping",
    "caravan_site": "area_autocaravana",
}

# Esquema común de oferta. Coincide con los campos de `establecimientos_turisticos` para
# que la carga posterior sea directa; los atributos propios del segmento van detrás.
COLUMNAS = [
    "id_fuente",
    "nombre",
    "tipo",
    "tipo_establecimiento",
    "lat",
    "lon",
    "direccion",
    "ccaa",
    "provincia",
    "municipio",
    "fuente_dato",
    "fecha_extraccion",
    # Atributos del segmento. Vacíos cuando OSM no los trae, que es lo habitual.
    "plazas",
    "plazas_tiendas",
    "plazas_caravanas",
    "admite_tiendas",
    "admite_caravanas",
    "admite_autocaravanas",
    "agua_potable",
    "electricidad",
    "vaciado_aguas",
    "aseos",
    "duchas",
    # Señales para separar oferta comercial de acampada libre. En Baleares, varios
    # `camp_site` son zonas de acampada públicas o campamentos, no campings de pago:
    # contarlos como oferta inflaría la capacidad del destino.
    "de_pago",
    "operador",
    "acampada_informal",
    "horario",
    "web",
    "telefono",
]


def primera_etiqueta(tags: dict, claves: tuple[str, ...]) -> str | None:
    """Primer valor presente de una lista de etiquetas alternativas de OSM."""
    for clave in claves:
        valor = tags.get(clave)
        if valor not in (None, ""):
            return valor
    return None


def a_entero(valor: object) -> object:
    """OSM guarda todo como texto libre; 'capacity=120' convive con 'capacity=aprox 120'."""
    if valor in (None, ""):
        return pd.NA
    try:
        return int(str(valor).strip())
    except ValueError:
        return pd.NA


def a_booleano(valor: object) -> object:
    """
    Traduce los valores yes/no/designated de OSM.

    Se distingue "no consta" de "no admite": dejar en blanco lo desconocido evita que un
    camping sin etiquetar aparezca como que prohíbe caravanas.
    """
    if valor in (None, ""):
        return pd.NA
    v = str(valor).strip().lower()
    if v in ("yes", "designated", "permissive", "customers", "1", "true"):
        return True
    if v in ("no", "0", "false", "prohibited"):
        return False
    return pd.NA


def coordenadas(el: dict) -> tuple[object, object]:
    """Coordenadas del elemento; para way y relation, el centroide que da `out center`."""
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    centro = el.get("center") or {}
    return centro.get("lat", pd.NA), centro.get("lon", pd.NA)


def normalizar(elementos: list[dict], nombre_ccaa: str, ahora: datetime) -> pd.DataFrame:
    filas = []
    for el in elementos:
        tags = el.get("tags", {})
        valor_osm = tags.get("tourism", "")
        if valor_osm not in TIPOS:
            continue

        lat, lon = coordenadas(el)

        # La dirección se recompone de addr:*; OSM rara vez la trae completa.
        calle = tags.get("addr:street")
        numero = tags.get("addr:housenumber")
        direccion = " ".join(p for p in (calle, numero) if p) or None

        filas.append({
            "id_fuente": f"{el['type']}/{el['id']}",
            "nombre": tags.get("name"),
            "tipo": TIPOS[valor_osm],
            # Valor del ENUM de la base de datos: un camping es alojamiento.
            "tipo_establecimiento": "alojamiento",
            "lat": lat,
            "lon": lon,
            "direccion": direccion,
            "ccaa": nombre_ccaa,
            # OSM no garantiza provincia ni municipio. Se toma de addr:* cuando existe; el
            # resto se resolverá por cruce espacial con los límites administrativos en la
            # fase de transformación, igual que el resto de capas.
            "provincia": tags.get("addr:province"),
            "municipio": primera_etiqueta(tags, ("addr:city", "addr:town", "addr:village")),
            "fuente_dato": "openstreetmap-overpass",
            "fecha_extraccion": ahora.isoformat(),
            "plazas": a_entero(primera_etiqueta(tags, ("capacity", "capacity:persons"))),
            "plazas_tiendas": a_entero(tags.get("capacity:tents")),
            "plazas_caravanas": a_entero(tags.get("capacity:caravans")),
            "admite_tiendas": a_booleano(tags.get("tents")),
            "admite_caravanas": a_booleano(tags.get("caravans")),
            "admite_autocaravanas": a_booleano(
                primera_etiqueta(tags, ("motorhome", "motor_vehicle"))),
            "agua_potable": a_booleano(
                primera_etiqueta(tags, ("drinking_water", "water_point"))),
            "electricidad": a_booleano(
                primera_etiqueta(tags, ("power_supply", "electricity"))),
            "vaciado_aguas": a_booleano(
                primera_etiqueta(tags, ("sanitary_dump_station", "waste_disposal"))),
            "aseos": a_booleano(tags.get("toilets")),
            "duchas": a_booleano(tags.get("shower")),
            "de_pago": a_booleano(tags.get("fee")),
            "operador": tags.get("operator"),
            # backcountry/impromptu marcan acampada libre o improvisada, no camping comercial.
            "acampada_informal": a_booleano(
                primera_etiqueta(tags, ("backcountry", "impromptu"))),
            "horario": tags.get("opening_hours"),
            "web": primera_etiqueta(tags, ("website", "contact:website", "url")),
            "telefono": primera_etiqueta(tags, ("phone", "contact:phone")),
        })

    return pd.DataFrame(filas, columns=COLUMNAS)


def informar(df: pd.DataFrame, elementos: list[dict]) -> None:
    total = len(df)
    if not total:
        print("  Sin elementos normalizados.")
        return

    print("\nPor tipo:")
    for tipo, n in df["tipo"].value_counts().items():
        print(f"  {tipo:<22} {n:>6,}")

    tipos_osm: dict[str, int] = {}
    for el in elementos:
        tipos_osm[el["type"]] = tipos_osm.get(el["type"], 0) + 1
    print(f"\nGeometría OSM: {tipos_osm}")

    con_coord = df["lat"].notna() & df["lon"].notna()
    print(f"Con coordenadas: {int(con_coord.sum()):,}/{total:,}")

    print("\nCobertura de atributos:")
    for columna in ("nombre", "plazas", "plazas_tiendas", "plazas_caravanas",
                    "admite_tiendas", "admite_caravanas", "admite_autocaravanas",
                    "agua_potable", "electricidad", "vaciado_aguas", "aseos", "duchas",
                    "de_pago", "operador", "acampada_informal", "horario",
                    "municipio", "provincia", "web", "telefono"):
        n = int(df[columna].notna().sum())
        print(f"  {columna:<22} {n:>6,}  ({100 * n / total:>5.1f} %)")

    # Aviso explícito: sin nombre ni operador, un punto es difícilmente verificable como
    # oferta real, y en esta capa eso es la norma, no la excepción.
    anonimos = int((df["nombre"].isna() & df["operador"].isna()).sum())
    if anonimos:
        print(f"\n  Sin nombre ni operador: {anonimos:,}/{total:,} "
              f"({100 * anonimos / total:.0f} %)")
    informales = int((df["acampada_informal"] == True).sum())  # noqa: E712
    if informales:
        print(f"  Marcados como acampada informal (no oferta comercial): {informales:,}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae campings y áreas de autocaravana de OSM por comunidad autónoma."
    )
    parser.add_argument("--ccaa", help=f"CCAA a extraer. Opciones: {', '.join(sorted(CCAA))}")
    parser.add_argument("--listar-ccaa", action="store_true", help="Lista las CCAA y sale.")
    parser.add_argument(
        "--permitir-vacio", action="store_true",
        help="Acepta 0 elementos en vez de reintentar. Ceuta y Melilla pueden no tener "
             "ningún camping, así que aquí el vacío puede ser real.",
    )
    args = parser.parse_args()

    if args.listar_ccaa:
        for slug, (iso, nombre) in sorted(CCAA.items()):
            print(f"  {slug:<20} {iso:<7} {nombre}")
        return 0

    if not args.ccaa:
        parser.error("indica --ccaa (o usa --listar-ccaa para ver las opciones)")

    slug = args.ccaa.strip().lower()
    if slug not in CCAA:
        print(f"CCAA desconocida: '{args.ccaa}'.", file=sys.stderr)
        print(f"Opciones válidas: {', '.join(sorted(CCAA))}", file=sys.stderr)
        return 1

    iso_code, nombre = CCAA[slug]
    print(f"Capa 'camping' — {nombre} ({iso_code})")
    for f in FILTROS:
        print(f"  {f}")

    inicio = time.time()
    try:
        datos = consultar_overpass(
            construir_query_filtros(iso_code, FILTROS),
            permitir_vacio=args.permitir_vacio,
        )
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    elementos = datos.get("elements", [])
    fichero_raw = guardar_raw(datos, slug, iso_code, nombre, FILTROS,
                              prefijo="camping", claves_resumen=("tourism",))

    ahora = datetime.now(timezone.utc)
    df = normalizar(elementos, nombre, ahora)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    fichero_norm = PROCESSED_DIR / f"camping_normalizado_{slug}.csv"
    df.to_csv(fichero_norm, index=False, encoding="utf-8")

    print(f"\nElementos extraídos: {len(elementos):,} en {time.time() - inicio:.1f}s")
    for etiqueta, n in resumen_por_clave(elementos, ("tourism",)).items():
        print(f"  {etiqueta:<22} {n:>6,}")

    informar(df, elementos)

    print(f"\nCrudo:      {fichero_raw.relative_to(PROJECT_ROOT)}")
    print(f"Normalizado: {fichero_norm.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
