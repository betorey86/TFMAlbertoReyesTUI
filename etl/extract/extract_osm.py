"""
Extracción de alojamientos turísticos desde OpenStreetMap (API Overpass).

Descarga los elementos con tourism=hotel|hostel|apartment|guest_house de una
comunidad autónoma española y guarda la respuesta cruda (sin transformar) en
data/raw/. La idea es ir extrayendo CCAA a CCAA para no saturar Overpass y poder
validar los datos por partes.

Uso:
    python etl/extract/extract_osm.py --ccaa baleares
    python etl/extract/extract_osm.py --ccaa baleares --tipos hotel hostel
    python etl/extract/extract_osm.py --listar-ccaa
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Raíz del repo: .../tfm-tui-dashboard (este fichero está en etl/extract/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Réplicas de respaldo: la instancia principal devuelve 429/504 con frecuencia.
OVERPASS_MIRRORS = [
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# Códigos ISO 3166-2 de las CCAA. Overpass los expone como etiqueta de la
# relación administrativa, y son mucho más fiables que buscar por nombre.
CCAA = {
    "andalucia": ("ES-AN", "Andalucía"),
    "aragon": ("ES-AR", "Aragón"),
    "asturias": ("ES-AS", "Principado de Asturias"),
    "baleares": ("ES-IB", "Illes Balears"),
    "canarias": ("ES-CN", "Canarias"),
    "cantabria": ("ES-CB", "Cantabria"),
    "castilla-la-mancha": ("ES-CM", "Castilla-La Mancha"),
    "castilla-y-leon": ("ES-CL", "Castilla y León"),
    "cataluna": ("ES-CT", "Cataluña"),
    "ceuta": ("ES-CE", "Ceuta"),
    "extremadura": ("ES-EX", "Extremadura"),
    "galicia": ("ES-GA", "Galicia"),
    "la-rioja": ("ES-RI", "La Rioja"),
    "madrid": ("ES-MD", "Comunidad de Madrid"),
    "melilla": ("ES-ML", "Melilla"),
    "murcia": ("ES-MC", "Región de Murcia"),
    "navarra": ("ES-NC", "Comunidad Foral de Navarra"),
    "pais-vasco": ("ES-PV", "País Vasco"),
    "valencia": ("ES-VC", "Comunitat Valenciana"),
}

TIPOS_ALOJAMIENTO = ["hotel", "hostel", "apartment", "guest_house"]

TIMEOUT_OVERPASS = 300  # segundos que damos al servidor para resolver la consulta
TIMEOUT_HTTP = 360      # algo mayor, para no cortar antes que el propio servidor


def construir_query_filtros(iso_code: str, filtros: list[str]) -> str:
    """
    Genera la consulta Overpass QL para una CCAA a partir de filtros de etiqueta ya
    formateados, p. ej. `["amenity"~"^(restaurant|cafe|bar)$"]`.

    Cada filtro se consulta sobre node, way y relation. `out center` devuelve el centroide
    de los polígonos, sin el cual los way y relation se quedarían sin coordenadas.

    Base común a todas las capas del proyecto (alojamiento, restauración, atracciones,
    transporte), para que todas hereden la misma selección territorial por ISO 3166-2.
    """
    bloques = []
    for f in filtros:
        for tipo in ("node", "way", "relation"):
            bloques.append(f"  {tipo}{f}(area.ccaa);")
    cuerpo = "\n".join(bloques)

    return f"""
[out:json][timeout:{TIMEOUT_OVERPASS}];
area["ISO3166-2"="{iso_code}"][admin_level=4]->.ccaa;
(
{cuerpo}
);
out center tags;
""".strip()


def construir_query(iso_code: str, tipos: list[str]) -> str:
    """Consulta de alojamientos turísticos (tourism=hotel|hostel|apartment|guest_house)."""
    return construir_query_filtros(iso_code, [f'["tourism"~"^({"|".join(tipos)})$"]'])


def consultar_overpass(query: str, reintentos: int = 3, permitir_vacio: bool = False) -> dict:
    """
    Lanza la consulta rotando entre réplicas si alguna falla o va saturada.

    Overpass puede responder HTTP 200 con un resultado inservible de dos formas:

    1. Incluyendo un campo "remark" con un error de ejecución (timeout, memoria).
    2. Devolviendo "elements": [] sin más, cuando la base de datos de áreas no está
       disponible en ese momento.

    Ambos casos se tratan como fallo y se reintentan: dar por buena una respuesta vacía
    significaría guardar en data/raw/ un fichero de 0 elementos como si fuera una
    extracción correcta. Ninguna CCAA española tiene 0 alojamientos, así que un
    resultado vacío es siempre un error, nunca un dato real.
    """
    ultimo_error: Exception | None = None

    for intento in range(reintentos):
        for url in OVERPASS_MIRRORS:
            print(f"  -> Consultando {url} (intento {intento + 1}/{reintentos})...")
            try:
                respuesta = requests.post(
                    url,
                    data={"data": query},
                    timeout=TIMEOUT_HTTP,
                    headers={"User-Agent": "TFM-TUI-Dashboard/0.1 (uso academico)"},
                )
            except requests.RequestException as exc:
                ultimo_error = exc
                print(f"     Fallo de red: {exc}")
                continue

            # 429 = demasiadas peticiones, 504 = la consulta agotó el tiempo del servidor.
            if respuesta.status_code in (429, 504):
                ultimo_error = RuntimeError(f"HTTP {respuesta.status_code} en {url}")
                print(f"     Servidor saturado (HTTP {respuesta.status_code}).")
                continue

            if not respuesta.ok:
                ultimo_error = RuntimeError(
                    f"HTTP {respuesta.status_code} en {url}: {respuesta.text[:300]}"
                )
                print(f"     Error HTTP {respuesta.status_code}.")
                continue

            try:
                datos = respuesta.json()
            except ValueError as exc:
                # Overpass devuelve HTML cuando rechaza la consulta.
                ultimo_error = RuntimeError(f"Respuesta no-JSON de {url}: {exc}")
                print("     La respuesta no es JSON válido.")
                continue

            # Error de ejecución señalado dentro de una respuesta 200.
            remark = datos.get("remark")
            if remark:
                ultimo_error = RuntimeError(f"Overpass devolvió un aviso: {remark}")
                print(f"     Aviso del servidor: {remark}")
                continue

            if not datos.get("elements") and not permitir_vacio:
                ultimo_error = RuntimeError(
                    f"{url} devolvió 0 elementos (respuesta degradada, no un dato real)"
                )
                print("     0 elementos: respuesta degradada, se reintenta.")
                continue

            return datos

        espera = 10 * (intento + 1)
        if intento < reintentos - 1:
            print(f"  Todas las réplicas fallaron. Reintentando en {espera}s...")
            time.sleep(espera)

    raise RuntimeError(f"No se pudo completar la consulta Overpass. Último error: {ultimo_error}")


def resumen_por_clave(elementos: list[dict], claves: tuple[str, ...] = ("tourism",)) -> dict[str, int]:
    """
    Cuenta elementos por el valor de la primera etiqueta presente de `claves`.

    Las capas usan claves distintas (tourism, amenity, railway...), así que la categoría se
    busca en orden y se etiqueta como `clave=valor` cuando hay más de una posible.
    """
    conteo: dict[str, int] = {}
    for el in elementos:
        tags = el.get("tags", {})
        etiqueta = "desconocido"
        for clave in claves:
            if clave in tags:
                etiqueta = f"{clave}={tags[clave]}" if len(claves) > 1 else tags[clave]
                break
        conteo[etiqueta] = conteo.get(etiqueta, 0) + 1
    return dict(sorted(conteo.items(), key=lambda kv: kv[1], reverse=True))


def resumen_por_tipo(elementos: list[dict]) -> dict[str, int]:
    """Recuento por tourism=*, para la capa de alojamientos."""
    return resumen_por_clave(elementos, ("tourism",))


def guardar_raw(
    datos: dict,
    slug: str,
    iso_code: str,
    nombre: str,
    tipos: list[str],
    prefijo: str = "alojamientos",
    claves_resumen: tuple[str, ...] = ("tourism",),
) -> Path:
    """Guarda la respuesta cruda junto a metadatos de trazabilidad."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    ahora = datetime.now(timezone.utc)
    elementos = datos.get("elements", [])

    salida = {
        "metadata": {
            "capa": prefijo,
            "ccaa_slug": slug,
            "ccaa_nombre": nombre,
            "iso3166_2": iso_code,
            "tipos_consultados": tipos,
            "fecha_extraccion": ahora.isoformat(),
            "fuente_dato": "openstreetmap-overpass",
            "num_elementos": len(elementos),
            "resumen_por_tipo": resumen_por_clave(elementos, claves_resumen),
        },
        "osm": datos,
    }

    fichero = RAW_DIR / f"osm_{prefijo}_{slug}_{ahora:%Y%m%d}.json"
    with fichero.open("w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    return fichero


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae alojamientos turísticos de OSM por comunidad autónoma."
    )
    parser.add_argument("--ccaa", help=f"CCAA a extraer. Opciones: {', '.join(sorted(CCAA))}")
    parser.add_argument(
        "--tipos",
        nargs="+",
        default=TIPOS_ALOJAMIENTO,
        help=f"Valores de tourism=* a extraer (por defecto: {' '.join(TIPOS_ALOJAMIENTO)})",
    )
    parser.add_argument("--listar-ccaa", action="store_true", help="Lista las CCAA disponibles y sale.")
    parser.add_argument(
        "--permitir-vacio",
        action="store_true",
        help="Acepta un resultado de 0 elementos en vez de reintentar. Útil sólo con "
             "filtros --tipos muy restrictivos donde el vacío puede ser real.",
    )
    args = parser.parse_args()

    if args.listar_ccaa:
        print("CCAA disponibles:")
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
    query = construir_query(iso_code, args.tipos)

    print(f"Extrayendo alojamientos de {nombre} ({iso_code})")
    print(f"Tipos: {', '.join(args.tipos)}")

    inicio = time.time()
    try:
        datos = consultar_overpass(query, permitir_vacio=args.permitir_vacio)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    elementos = datos.get("elements", [])
    if not elementos:
        print("\nAVISO: la consulta no devolvió elementos. Revisa el código ISO o los tipos.")

    fichero = guardar_raw(datos, slug, iso_code, nombre, args.tipos)

    print(f"\nElementos extraídos: {len(elementos)} en {time.time() - inicio:.1f}s")
    for tipo, n in resumen_por_tipo(elementos).items():
        print(f"  {tipo:<15} {n}")
    print(f"\nGuardado en: {fichero.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
