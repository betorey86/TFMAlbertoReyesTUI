"""
Geocodificación de registros de VUT sin coordenadas, usando Nominatim (OpenStreetMap).

Algunos registros oficiales sólo publican dirección postal (el censo del País Vasco no trae
ni una sola coordenada). Sin lat/lon no sirven para nada espacial, así que hay que
geocodificarlos antes de poder calcular densidades de oferta.

Política de uso de Nominatim (https://operations.osmfoundation.org/policies/nominatim/):
máximo 1 petición por segundo y User-Agent identificable. Ambas se respetan aquí. Es un
servicio gratuito mantenido por donaciones: si más adelante hay que geocodificar volúmenes
mayores (Canarias tiene 24.000 registros sin coordenadas), lo correcto es levantar una
instancia propia de Nominatim o usar un servicio de pago, no acelerar este script.

Uso:
    python etl/transform/geocode_direcciones.py --fuente pais_vasco
    python etl/transform/geocode_direcciones.py --fuente pais_vasco --limite 50
    python etl/transform/geocode_direcciones.py --fuente pais_vasco --solo-resumen
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from geopy.exc import GeocoderQuotaExceeded, GeocoderServiceError, GeocoderTimedOut
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CACHE_DIR = PROCESSED_DIR / "geocache"

# 1 req/s es el máximo que permite la política; 1.1 deja margen de seguridad.
DELAY_SEGUNDOS = 1.1

UA_POR_DEFECTO = "tfm-tui-dashboard/0.1 (proyecto academico TFM; geocodificacion de VUT)"

# Fuentes normalizadas que pueden geocodificarse. La clave es el sufijo del fichero
# data/processed/vut_normalizado_<clave>.csv
FUENTES = {
    "pais_vasco": "País Vasco",
    "canarias": "Canarias",
    "baleares": "Illes Balears",
    "andalucia": "Andalucía",
}

# Restos de dirección que estorban a Nominatim: planta, puerta, mano.
RE_PARENTESIS = re.compile(r"\s*\([^)]*\)\s*")
RE_PISO = re.compile(
    r",?\s*\b("
    r"\d+\s*[ºªoa]\s*(izq|izda|izqda|dcha|dch|dr|centro|ctro|[a-z])?"  # 1º B, 3ª Izda
    r"|entlo|entresuelo|bajo|bj|sotano|sótano|atico|ático|pral|principal"
    r"|esc(alera)?\.?\s*\w*"
    r")\b\.?",
    re.IGNORECASE,
)

# El censo vasco pega la mano al número sin separador ('Solokoetxe, 6IZ', '52-DR').
# Nominatim interpreta '6IZ' como parte del nombre de la vía y no encuentra nada.
RE_MANO_PEGADA = re.compile(r"\b(\d+)\s*[-\s]?(iz|izq|izda|dr|dch|dcha)\b\.?", re.IGNORECASE)

# Componentes finales que son planta y/o puerta: ', B', ', B IZ', ', B A', ', 1 D'.
RE_PUERTA_FINAL = re.compile(r",\s*[a-zA-Z]{1,2}(\s+[a-zA-Z]{1,2})?\s*$")

# Genéricos de vía en euskera y castellano. Se usan para generar una variante sin ellos:
# los barrios rurales ('Auzoa/Barrio Gendika') están en OSM como el topónimo a secas.
GENERICOS = (
    "auzoa", "barrio", "kalea", "calle", "plaza", "enparantza", "etorbidea", "avenida",
    "ibilbidea", "paseo", "errepidea", "carretera", "zumardia", "alameda", "bidea",
)

# Abreviaturas de tipo de vía. Nominatim resuelve mejor la forma desarrollada.
# El punto va dentro del patrón para que no quede huérfano ('Pl. X' -> 'Plaza X').
ABREVIATURAS = {
    r"\bpl\.(?=\s)": "Plaza",
    r"\bpza\.?(?=\s)": "Plaza",
    r"\betorb\.?(?=\s)": "Avenida",
    r"\bavda\.?(?=\s)": "Avenida",
    r"\bavd\.?(?=\s)": "Avenida",
    r"\bav\.(?=\s)": "Avenida",
    r"\bctra\.?(?=\s)": "Carretera",
    r"\berrep\.?(?=\s)": "Carretera",
    r"\bc/\s*": "Calle ",
    r"\bbº(?=\s)": "Barrio",
}


def normalizar(texto: str | float | None) -> str:
    """Minúsculas sin acentos ni signos, para comparar topónimos entre fuentes."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def limpiar_direccion(direccion: str) -> str:
    """
    Deja la dirección en 'calle, número'.

    El censo vasco escribe cosas como 'Bidebarrieta, 7, 1º DR (Bilbao)': el paréntesis
    duplica el municipio (que ya tenemos en su propia columna) y el '1º DR' es la puerta,
    que Nominatim no sabe interpretar y que empeora la búsqueda.
    """
    s = RE_PARENTESIS.sub(" ", str(direccion))
    s = RE_PISO.sub("", s)
    s = RE_MANO_PEGADA.sub(r"\1", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    # Se aplica dos veces: 'Goienkale, 37, B A' deja primero ', B A' y luego nada.
    s = RE_PUERTA_FINAL.sub("", RE_PUERTA_FINAL.sub("", s))
    for patron, reemplazo in ABREVIATURAS.items():
        s = re.sub(patron, reemplazo, s, flags=re.IGNORECASE)
    s = re.sub(r"(,\s*)+$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip(" ,")


def desdoblar_bilingue(direccion: str) -> str | None:
    """
    Devuelve la variante castellana de una vía con nombre doble, o None si no la hay.

    El censo vasco escribe los genéricos en las dos lenguas separados por barra
    ('Zumardia/Alameda Mazarredo', 'Auzoa/Barrio Gendika'). Nominatim no reconoce la
    cadena con barra, pero sí cada forma por separado.
    """
    if "/" not in direccion:
        return None
    # Sólo la primera barra separa los genéricos; el resto de la dirección se conserva.
    izquierda, _, derecha = direccion.partition("/")
    # 'Zumardia/Alameda Mazarredo' -> 'Alameda Mazarredo'; se descarta el genérico vasco.
    alternativa = derecha.strip()
    return alternativa if alternativa else None


def construir_consulta(fila: pd.Series, direccion: str | None = None) -> str:
    """Compone la cadena que se envía a Nominatim."""
    base = direccion if direccion is not None else limpiar_direccion(fila.get("direccion", ""))
    partes = [base]
    for campo in ("municipio", "provincia"):
        valor = fila.get(campo)
        if valor and not pd.isna(valor) and normalizar(valor) not in normalizar(base):
            partes.append(str(valor))
    partes.append("España")
    return ", ".join(p for p in partes if p)


def variantes(fila: pd.Series) -> list[str]:
    """
    Consultas a probar en orden, de más a menos específica.

    Se para en la primera que devuelva resultado. No se incluye el municipio a secas como
    último recurso: devolvería el centroide del pueblo para todas sus viviendas, que es
    precisión falsa y desplazaría los cálculos de densidad.
    """
    limpia = limpiar_direccion(fila.get("direccion", ""))
    consultas = [construir_consulta(fila, limpia)]

    alternativa = desdoblar_bilingue(limpia)
    if alternativa:
        consultas.append(construir_consulta(fila, alternativa))

    # Sin el genérico de vía: los barrios rurales ('Auzoa/Barrio Gendika') figuran en OSM
    # como el topónimo solo ('Gendika').
    base = alternativa or limpia
    sin_generico = re.sub(
        rf"^\s*({'|'.join(GENERICOS)})\s+", "", base, flags=re.IGNORECASE
    ).strip()
    if sin_generico and sin_generico != base:
        consultas.append(construir_consulta(fila, sin_generico))

    # Sin número de portal: sitúa en la vía, que para densidad municipal es suficiente.
    sin_numero = re.sub(r",\s*\d+[a-zA-Z\-]*\s*$", "", limpia).strip(" ,")
    if sin_numero and sin_numero != limpia:
        consultas.append(construir_consulta(fila, sin_numero))

    vistas, unicas = set(), []
    for c in consultas:
        if c not in vistas:
            vistas.add(c)
            unicas.append(c)
    return unicas


# ---------------------------------------------------------------------------
# Caché reanudable
# ---------------------------------------------------------------------------

def cargar_cache(fichero: Path) -> dict[str, dict]:
    """Lee la caché JSONL. Una línea corrupta (corte a media escritura) se ignora."""
    if not fichero.exists():
        return {}
    cache: dict[str, dict] = {}
    with fichero.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                reg = json.loads(linea)
            except ValueError:
                continue
            cache[reg["consulta"]] = reg
    return cache


def anexar_cache(fichero: Path, registro: dict) -> None:
    """Escribe y hace flush inmediato: si el proceso muere, no se pierde lo ya resuelto."""
    fichero.parent.mkdir(parents=True, exist_ok=True)
    with fichero.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Geocodificación
# ---------------------------------------------------------------------------

CAMPOS_MUNICIPIO = ("city", "town", "village", "municipality", "city_district", "suburb")


def municipio_de_respuesta(direccion: dict) -> str:
    for campo in CAMPOS_MUNICIPIO:
        if direccion.get(campo):
            return str(direccion[campo])
    return ""


def evaluar_confianza(municipio_original: str, respuesta: dict | None) -> tuple[str, str]:
    """
    Devuelve (confianza, municipio_devuelto).

    alta  -> el municipio devuelto por Nominatim coincide con el del registro oficial
    baja  -> hay coordenadas pero el municipio no coincide: el punto puede estar en otro
             sitio, así que no debe usarse sin revisión
    nula  -> Nominatim no encontró nada
    """
    if respuesta is None:
        return "nula", ""

    devuelto = municipio_de_respuesta(respuesta.get("address", {}))
    a, b = normalizar(municipio_original), normalizar(devuelto)
    if a and b and (a == b or a in b or b in a):
        return "alta", devuelto
    return "baja", devuelto


def geocodificar(
    mapa_variantes: dict[str, list[str]], cache_path: Path, limite: int | None
) -> dict[str, dict]:
    """
    Resuelve cada dirección probando sus variantes en orden hasta que una devuelva algo.

    La caché se indexa por la consulta canónica (la primera variante), de modo que al
    reanudar no se repite ninguna dirección ya resuelta.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    user_agent = os.getenv("NOMINATIM_USER_AGENT", UA_POR_DEFECTO)

    geolocator = Nominatim(user_agent=user_agent, timeout=30)
    geocode = RateLimiter(
        geolocator.geocode,
        min_delay_seconds=DELAY_SEGUNDOS,
        max_retries=2,
        error_wait_seconds=10.0,
        swallow_exceptions=False,
    )

    cache = cargar_cache(cache_path)
    canonicas = list(mapa_variantes)
    pendientes = [c for c in canonicas if c not in cache]

    if limite is not None:
        pendientes = pendientes[:limite]

    print(f"  Direcciones únicas: {len(canonicas):,}")
    print(f"  Ya en caché:        {len(canonicas) - len([c for c in canonicas if c not in cache]):,}")
    print(f"  Pendientes:         {len(pendientes):,}")
    if pendientes:
        media_variantes = sum(len(mapa_variantes[c]) for c in pendientes) / len(pendientes)
        print(f"  Tiempo estimado:    {len(pendientes) * media_variantes * DELAY_SEGUNDOS / 60:.0f} min "
              f"(1 petición/s, política de Nominatim)\n")

    aciertos = 0
    for i, canonica in enumerate(pendientes, start=1):
        loc = None
        usada = None
        fallo_red = False

        for consulta in mapa_variantes[canonica]:
            try:
                loc = geocode(consulta, addressdetails=True, country_codes="es", exactly_one=True)
            except (GeocoderTimedOut, GeocoderServiceError, GeocoderQuotaExceeded) as exc:
                # Fallo de red o de servicio, no una dirección irresoluble: no se cachea,
                # para que la siguiente ejecución vuelva a intentarlo.
                print(f"  [{i}/{len(pendientes)}] ERROR {type(exc).__name__}: {consulta[:60]}")
                time.sleep(5)
                fallo_red = True
                break
            if loc:
                usada = consulta
                break

        if fallo_red:
            continue

        registro = {
            "consulta": canonica,
            "consulta_usada": usada,
            "variantes_probadas": len(mapa_variantes[canonica]) if not loc else
                                  mapa_variantes[canonica].index(usada) + 1,
            "lat": loc.latitude if loc else None,
            "lon": loc.longitude if loc else None,
            "address": loc.raw.get("address", {}) if loc else {},
            "display_name": loc.address if loc else None,
            "osm_type": loc.raw.get("osm_type") if loc else None,
            "fecha": datetime.now(timezone.utc).isoformat(),
        }
        cache[canonica] = registro
        anexar_cache(cache_path, registro)

        if loc:
            aciertos += 1
        if i % 100 == 0 or i == len(pendientes):
            print(f"  [{i}/{len(pendientes)}] {aciertos} con resultado "
                  f"({100 * aciertos / i:.0f}%)")

    return cache


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def resumen(df: pd.DataFrame, duracion: float, slug: str) -> None:
    total = len(df)
    con_coord = df["lat_geocod"].notna().sum()
    alta = (df["geocoding_confianza"] == "alta").sum()
    baja = (df["geocoding_confianza"] == "baja").sum()
    nula = (df["geocoding_confianza"] == "nula").sum()

    print("\n" + "=" * 72)
    print(f"RESUMEN DE GEOCODIFICACIÓN — {FUENTES.get(slug, slug)}")
    print("=" * 72)
    print(f"  Registros:                {total:>8,}")
    print(f"  Geocodificados:           {con_coord:>8,}  ({100 * con_coord / total:.1f} %)")
    print(f"    - confianza alta:       {alta:>8,}  ({100 * alta / total:.1f} %)")
    print(f"    - confianza baja:       {baja:>8,}  ({100 * baja / total:.1f} %)")
    print(f"  Sin resultado:            {nula:>8,}  ({100 * nula / total:.1f} %)")
    print(f"\n  Duración: {duracion / 60:.1f} min")

    if baja:
        print("\n  Muestra de baja confianza (municipio devuelto != municipio oficial):")
        cols = ["municipio", "geocoding_municipio_devuelto", "direccion"]
        muestra = df[df["geocoding_confianza"] == "baja"][cols].head(5)
        for _, fila in muestra.iterrows():
            print(f"    {fila['municipio']} -> {fila['geocoding_municipio_devuelto']}"
                  f"  ({str(fila['direccion'])[:45]})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Geocodifica con Nominatim los registros de VUT sin coordenadas."
    )
    parser.add_argument("--fuente", default="pais_vasco",
                        help=f"Fuente normalizada a geocodificar: {', '.join(FUENTES)}")
    parser.add_argument("--limite", type=int,
                        help="Geocodifica como mucho N direcciones nuevas (para pruebas).")
    parser.add_argument("--solo-resumen", action="store_true",
                        help="No consulta a Nominatim: sólo recompone la salida desde la caché.")
    args = parser.parse_args()

    slug = args.fuente
    entrada = PROCESSED_DIR / f"vut_normalizado_{slug}.csv"
    if not entrada.exists():
        print(f"ERROR: no existe {entrada}", file=sys.stderr)
        print("Ejecuta antes etl/extract/extract_vut_oficial.py", file=sys.stderr)
        return 1

    df = pd.read_csv(entrada, low_memory=False)
    print(f"{FUENTES.get(slug, slug)}: {len(df):,} registros en {entrada.name}")

    # Sólo se geocodifica lo que no tiene coordenadas.
    sin_coord = df["lat"].isna() | df["lon"].isna()
    print(f"  Sin coordenadas: {sin_coord.sum():,}")
    if not sin_coord.any():
        print("  Nada que geocodificar.")
        return 0

    objetivo = df[sin_coord].copy()
    listas = objetivo.apply(variantes, axis=1)
    objetivo["consulta"] = [v[0] for v in listas]

    mapa_variantes: dict[str, list[str]] = {}
    for v in listas:
        mapa_variantes.setdefault(v[0], v)

    cache_path = CACHE_DIR / f"geocache_{slug}.jsonl"
    inicio = time.time()

    if args.solo_resumen:
        cache = cargar_cache(cache_path)
        print(f"  Caché: {len(cache):,} consultas resueltas")
    else:
        cache = geocodificar(mapa_variantes, cache_path, args.limite)

    # Volcado de la caché a las columnas de salida
    resueltas = objetivo["consulta"].map(lambda c: cache.get(c))
    objetivo["lat_geocod"] = [r["lat"] if r else None for r in resueltas]
    objetivo["lon_geocod"] = [r["lon"] if r else None for r in resueltas]

    confianzas, devueltos = [], []
    for municipio, r in zip(objetivo["municipio"], resueltas):
        if r is None or r.get("lat") is None:
            confianzas.append("nula")
            devueltos.append("")
        else:
            c, d = evaluar_confianza(municipio, r)
            confianzas.append(c)
            devueltos.append(d)
    objetivo["geocoding_confianza"] = confianzas
    objetivo["geocoding_municipio_devuelto"] = devueltos

    # lat/lon definitivas: sólo se rellenan con la geocodificación de confianza alta.
    # Las de confianza baja se conservan en lat_geocod/lon_geocod para poder revisarlas,
    # pero no se dan por buenas: un punto en el municipio equivocado falsea la densidad.
    fiables = objetivo["geocoding_confianza"] == "alta"
    objetivo.loc[fiables, "lat"] = objetivo.loc[fiables, "lat_geocod"]
    objetivo.loc[fiables, "lon"] = objetivo.loc[fiables, "lon_geocod"]
    objetivo["necesita_geocodificacion"] = objetivo["lat"].isna()
    objetivo["geocoding_fuente"] = "nominatim-osm"
    objetivo["geocoding_fecha"] = datetime.now(timezone.utc).date().isoformat()

    salida = PROCESSED_DIR / f"vut_{slug}_geocodificado.csv"
    objetivo.to_csv(salida, index=False, encoding="utf-8")

    resumen(objetivo, time.time() - inicio, slug)
    print(f"\n  Salida: {salida.relative_to(PROJECT_ROOT)}")
    print(f"  Caché:  {cache_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
