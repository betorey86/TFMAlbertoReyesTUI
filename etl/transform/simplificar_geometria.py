"""
Versión simplificada de la geometría municipal, para las coropletas del dashboard.

`municipios_ine.geojson` pesa 178 MB porque procede del seccionado censal, cuya precisión
está pensada para el trabajo estadístico, no para dibujar. A escala de mapa nacional esa
precisión no se ve, pero el navegador tiene que descargarla y renderizarla igual.

La geometría precisa se conserva para el join espacial y la carga en PostGIS; ésta es sólo
para pintar.

Uso:
    python etl/transform/simplificar_geometria.py
    python etl/transform/simplificar_geometria.py --tolerancia 300
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

ENTRADA = PROCESSED_DIR / "municipios_ine.geojson"
SALIDA = PROCESSED_DIR / "municipios_simplificado.geojson"

# Metros. 250 m es imperceptible en un mapa de España y reduce el fichero un orden de
# magnitud. Se simplifica en un CRS proyectado: hacerlo en grados deformaría el norte.
TOLERANCIA_M = 250


def main() -> int:
    parser = argparse.ArgumentParser(description="Simplifica la geometría municipal.")
    parser.add_argument("--tolerancia", type=float, default=TOLERANCIA_M,
                        help=f"Tolerancia de simplificación en metros ({TOLERANCIA_M} por defecto).")
    args = parser.parse_args()

    if not ENTRADA.exists():
        print(f"ERROR: falta {ENTRADA}", file=sys.stderr)
        print("Ejecuta antes: python etl/extract/extract_ine_municipios.py", file=sys.stderr)
        return 1

    print(f"Leyendo {ENTRADA.name} ({ENTRADA.stat().st_size / 1e6:.0f} MB)…")
    gdf = gpd.read_file(ENTRADA)
    print(f"  {len(gdf):,} municipios".replace(",", "."))

    # Sólo lo necesario para pintar y para cruzar con los indicadores.
    columnas = [c for c in ("codigo_ine", "nombre_municipio", "provincia", "ccaa")
                if c in gdf.columns]
    gdf = gdf[columnas + ["geometry"]]

    print(f"Simplificando con tolerancia de {args.tolerancia:.0f} m…")
    proyectado = gdf.to_crs("EPSG:25830")
    # preserve_topology evita que los municipios pequeños desaparezcan o se solapen.
    proyectado["geometry"] = proyectado.geometry.simplify(
        args.tolerancia, preserve_topology=True
    )
    gdf = proyectado.to_crs("EPSG:4326")

    vacias = int(gdf.geometry.is_empty.sum() + gdf.geometry.isna().sum())
    if vacias:
        print(f"  AVISO: {vacias} geometrías quedaron vacías; se conserva la original.")
        originales = gpd.read_file(ENTRADA)
        idx = gdf.geometry.is_empty | gdf.geometry.isna()
        gdf.loc[idx, "geometry"] = originales.loc[idx, "geometry"].values

    if SALIDA.exists():
        SALIDA.unlink()
    gdf.to_file(SALIDA, driver="GeoJSON")

    antes = ENTRADA.stat().st_size / 1e6
    despues = SALIDA.stat().st_size / 1e6
    print(f"\n  {ENTRADA.name}: {antes:.0f} MB")
    print(f"  {SALIDA.name}: {despues:.1f} MB  ({100 * despues / antes:.1f} % del original)")
    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
