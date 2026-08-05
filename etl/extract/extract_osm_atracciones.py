"""
Capa de atracciones desde OpenStreetMap: recursos que motivan la visita.

Cubre tourism=attraction|museum|viewpoint y historic=monument. Alimenta el eje de "demanda
potencial" del dashboard: zonas con recursos pero poca oferta de alojamiento son candidatas
a inversión; zonas con muchos recursos y mucha oferta son las que hay que vigilar por
saturación.

Uso:
    python etl/extract/extract_osm_atracciones.py --ccaa baleares
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _capa_osm import Capa, ejecutar

CAPA = Capa(
    prefijo="atracciones",
    descripcion="Extrae atracciones, museos, monumentos y miradores de OSM por CCAA.",
    perfiles={
        "estandar": [
            '["tourism"~"^(attraction|museum|viewpoint)$"]',
            '["historic"="monument"]',
        ],
    },
    perfil_por_defecto="estandar",
    # Un elemento puede llevar tourism y historic a la vez (un monumento que además es
    # attraction). Se cuenta por tourism si existe, y sólo si no, por historic.
    claves_resumen=("tourism", "historic"),
)


if __name__ == "__main__":
    raise SystemExit(ejecutar(CAPA))
