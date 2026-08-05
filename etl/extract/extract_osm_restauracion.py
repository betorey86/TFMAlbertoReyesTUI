"""
Capa de restauración desde OpenStreetMap: restaurantes, cafeterías y bares.

Indicador de densidad de servicio turístico en el destino. En España el bar de barrio y el
restaurante turístico comparten etiqueta, así que esta capa mide oferta de hostelería en
general, no oferta orientada al visitante: sirve para densidad relativa entre zonas, no como
medida directa de especialización turística.

Uso:
    python etl/extract/extract_osm_restauracion.py --ccaa baleares
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _capa_osm import Capa, ejecutar

CAPA = Capa(
    prefijo="restauracion",
    descripcion="Extrae restaurantes, cafeterías y bares de OSM por comunidad autónoma.",
    perfiles={
        "estandar": ['["amenity"~"^(restaurant|cafe|bar)$"]'],
    },
    perfil_por_defecto="estandar",
    claves_resumen=("amenity",),
)


if __name__ == "__main__":
    raise SystemExit(ejecutar(CAPA))
