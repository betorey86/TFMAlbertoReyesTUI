"""
Capa de transporte desde OpenStreetMap: accesibilidad del destino.

El volumen es el problema de esta capa. `public_transport=stop_position` incluye cada poste
de autobús y cada punto de parada de cada línea, y para el análisis territorial eso no aporta
nada: lo relevante es dónde hay nodos de entrada al destino (estaciones de tren, estaciones
de autobuses, intercambiadores), no cuántas marquesinas tiene una avenida.

Por eso hay dos perfiles:

  principales (por defecto)
      railway=station, railway=halt, amenity=bus_station, public_transport=station.
      Estaciones e intercambiadores. Es lo que debería usarse para accesibilidad.

  completo
      Añade public_transport=stop_position y highway=bus_stop. Multiplica el volumen por
      uno o dos órdenes de magnitud. Sólo tiene sentido para análisis intraurbano de
      cobertura de transporte, no para comparar destinos.

Uso:
    python etl/extract/extract_osm_transporte.py --ccaa baleares
    python etl/extract/extract_osm_transporte.py --ccaa baleares --perfil completo
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _capa_osm import Capa, ejecutar

FILTROS_PRINCIPALES = [
    '["railway"~"^(station|halt)$"]',
    '["amenity"="bus_station"]',
    '["public_transport"="station"]',
]

CAPA = Capa(
    prefijo="transporte",
    descripcion="Extrae estaciones y nodos de transporte de OSM por comunidad autónoma.",
    perfiles={
        "principales": FILTROS_PRINCIPALES,
        "completo": FILTROS_PRINCIPALES + [
            '["public_transport"="stop_position"]',
            '["highway"="bus_stop"]',
        ],
    },
    perfil_por_defecto="principales",
    claves_resumen=("railway", "amenity", "public_transport", "highway"),
)


if __name__ == "__main__":
    raise SystemExit(ejecutar(CAPA))
