"""
Base común de las capas temáticas de OSM (restauración, atracciones, transporte).

Todas comparten la misma mecánica que `extract_osm.py`: selección territorial por código
ISO 3166-2, rotación entre réplicas de Overpass, reintentos ante respuestas vacías o con
`remark`, y `out center` para que los polígonos no se queden sin coordenadas. Aquí sólo
cambian las etiquetas consultadas y el nombre del fichero de salida.

No se ejecuta directamente: lo usan los scripts extract_osm_<capa>.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_osm import (
    CCAA,
    PROJECT_ROOT,
    construir_query_filtros,
    consultar_overpass,
    guardar_raw,
    resumen_por_clave,
)


@dataclass(frozen=True)
class Capa:
    """Definición de una capa temática."""
    prefijo: str                       # nombre en el fichero: osm_<prefijo>_<ccaa>_<fecha>.json
    descripcion: str
    perfiles: dict[str, list[str]]     # nombre de perfil -> filtros Overpass
    perfil_por_defecto: str
    claves_resumen: tuple[str, ...]    # etiquetas por las que se agrupa el recuento


def ejecutar(capa: Capa, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=capa.descripcion)
    parser.add_argument("--ccaa", help=f"CCAA a extraer. Opciones: {', '.join(sorted(CCAA))}")
    parser.add_argument(
        "--perfil",
        choices=sorted(capa.perfiles),
        default=capa.perfil_por_defecto,
        help=f"Conjunto de etiquetas a consultar (por defecto: {capa.perfil_por_defecto}).",
    )
    parser.add_argument("--listar-ccaa", action="store_true", help="Lista las CCAA y sale.")
    parser.add_argument(
        "--permitir-vacio",
        action="store_true",
        help="Acepta 0 elementos en vez de reintentar. Sólo para filtros muy restrictivos.",
    )
    args = parser.parse_args(argv)

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
    filtros = capa.perfiles[args.perfil]

    print(f"Capa '{capa.prefijo}' — {nombre} ({iso_code})")
    print(f"Perfil: {args.perfil}")
    for f in filtros:
        print(f"  {f}")

    inicio = time.time()
    try:
        datos = consultar_overpass(
            construir_query_filtros(iso_code, filtros),
            permitir_vacio=args.permitir_vacio,
        )
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    elementos = datos.get("elements", [])
    fichero = guardar_raw(
        datos, slug, iso_code, nombre, filtros,
        prefijo=f"{capa.prefijo}_{args.perfil}" if len(capa.perfiles) > 1 else capa.prefijo,
        claves_resumen=capa.claves_resumen,
    )

    print(f"\nElementos extraídos: {len(elementos):,} en {time.time() - inicio:.1f}s")
    for etiqueta, n in resumen_por_clave(elementos, capa.claves_resumen).items():
        print(f"  {etiqueta:<32} {n:>7,}")

    tipos_osm: dict[str, int] = {}
    for el in elementos:
        tipos_osm[el["type"]] = tipos_osm.get(el["type"], 0) + 1
    print(f"  {'(node/way/relation)':<32} {tipos_osm}")

    print(f"\nGuardado en: {fichero.relative_to(PROJECT_ROOT)}")
    return 0
