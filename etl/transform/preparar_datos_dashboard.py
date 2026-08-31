"""
Prepara el conjunto mínimo de datos que el dashboard necesita para desplegarse.

El cuadro de mando lee en local los ficheros de `data/raw/` y `data/processed/`, que suman
777 MB y que están excluidos del control de versiones porque se regeneran con el ETL. Eso
sirve para trabajar en la máquina de desarrollo, pero no para publicar la aplicación: un
servicio de despliegue necesita que los datos viajen en el repositorio.

Este script genera `data/dashboard/`, una carpeta autocontenida y ligera con lo
estrictamente necesario:

    indicadores_municipales.csv       los indicadores ya calculados
    municipios_simplificado.geojson   geometría a 250 m de tolerancia
    puntos.csv.gz                     las capas de punto consolidadas y comprimidas

El fichero de puntos sustituye a la lectura de los 72 MB de JSON de OpenStreetMap y los
73 MB de CSV de registros oficiales que la vista de detalle recorre en local. Se conserva
sólo lo que el mapa usa —capa, coordenadas y denominación— y se redondean las coordenadas a
cinco decimales, precisión de aproximadamente un metro, sobradamente suficiente para un
mapa de puntos con agrupación.

Uso:
    python etl/transform/preparar_datos_dashboard.py
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DASHBOARD_DIR = PROJECT_ROOT / "data" / "dashboard"

# Precisión de las coordenadas en el fichero de despliegue. Cinco decimales equivalen a algo
# más de un metro; el mapa agrupa los puntos, así que más precisión sólo añadiría peso.
DECIMALES = 5

CAPAS_OSM = {
    "alojamientos": "Alojamientos (OSM)",
    "restauracion": "Restauración (OSM)",
    "atracciones": "Atracciones (OSM)",
    "transporte_principales": "Transporte (OSM)",
    "camping": "Camping",
}

FICHEROS_DIRECTOS = [
    "indicadores_municipales.csv",
    "municipios_simplificado.geojson",
]


def recopilar_puntos() -> pd.DataFrame:
    """Consolida todas las capas de punto en una única tabla."""
    filas = []

    for prefijo, etiqueta in CAPAS_OSM.items():
        # Una CCAA puede tener varias fechas de extracción: se queda la más reciente.
        por_ccaa: dict[str, Path] = {}
        for ruta in glob.glob(str(RAW_DIR / f"osm_{prefijo}_*.json")):
            fichero = Path(ruta)
            if "consolidado" in fichero.name:
                continue
            slug = fichero.stem.split("_")[-2]
            if slug not in por_ccaa or fichero.name > por_ccaa[slug].name:
                por_ccaa[slug] = fichero

        n = 0
        for fichero in por_ccaa.values():
            try:
                with fichero.open(encoding="utf-8") as fh:
                    datos = json.load(fh)
            except (ValueError, OSError):
                print(f"    aviso: {fichero.name} ilegible, se omite")
                continue
            for el in datos.get("osm", {}).get("elements", []):
                centro = el.get("center") or {}
                lat = el.get("lat", centro.get("lat"))
                lon = el.get("lon", centro.get("lon"))
                if lat is None or lon is None:
                    continue
                filas.append({
                    "capa": etiqueta,
                    "lat": round(float(lat), DECIMALES),
                    "lon": round(float(lon), DECIMALES),
                    "nombre": el.get("tags", {}).get("name"),
                })
                n += 1
        print(f"  {etiqueta:<24} {n:>8,}".replace(",", "."))

    n = 0
    for fichero in sorted(PROCESSED_DIR.glob("vut_normalizado_*.csv")):
        try:
            df = pd.read_csv(fichero, usecols=lambda c: c in ("lat", "lon", "nombre"),
                             low_memory=False)
        except (ValueError, OSError):
            continue
        if "lat" not in df.columns:
            continue
        df = df[df["lat"].notna() & df["lon"].notna()]
        for r in df.itertuples(index=False):
            filas.append({
                "capa": "VUT registro oficial",
                "lat": round(float(r.lat), DECIMALES),
                "lon": round(float(r.lon), DECIMALES),
                "nombre": getattr(r, "nombre", None),
            })
            n += 1
    print(f"  {'VUT registro oficial':<24} {n:>8,}".replace(",", "."))

    return pd.DataFrame(filas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera data/dashboard/ con los datos mínimos para desplegar."
    )
    parser.add_argument("--sin-puntos", action="store_true",
                        help="No regenera el fichero de puntos (es el más lento).")
    args = parser.parse_args()

    faltan = [f for f in FICHEROS_DIRECTOS if not (PROCESSED_DIR / f).exists()]
    if faltan:
        print(f"ERROR: faltan ficheros en data/processed/: {faltan}", file=sys.stderr)
        print("Ejecuta antes calcular_indicadores.py y simplificar_geometria.py",
              file=sys.stderr)
        return 1

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/2] Copiando ficheros de indicadores y geometría")
    import shutil
    for nombre in FICHEROS_DIRECTOS:
        origen = PROCESSED_DIR / nombre
        destino = DASHBOARD_DIR / nombre
        shutil.copy2(origen, destino)
        print(f"  {nombre:<36} {destino.stat().st_size / 1e6:>7.1f} MB")

    if not args.sin_puntos:
        print("\n[2/2] Consolidando capas de punto")
        puntos = recopilar_puntos()
        destino = DASHBOARD_DIR / "puntos.csv.gz"
        puntos.to_csv(destino, index=False, encoding="utf-8", compression="gzip")
        print(f"\n  {len(puntos):,} puntos".replace(",", ".")
              + f" -> {destino.name}  {destino.stat().st_size / 1e6:.1f} MB")

    total = sum(f.stat().st_size for f in DASHBOARD_DIR.glob("*"))
    print("\n" + "=" * 62)
    print("DATOS PARA DESPLIEGUE")
    print("=" * 62)
    for f in sorted(DASHBOARD_DIR.glob("*")):
        print(f"  {f.name:<36} {f.stat().st_size / 1e6:>7.1f} MB")
    print(f"  {'TOTAL':<36} {total / 1e6:>7.1f} MB")
    print(f"\n  Carpeta: {DASHBOARD_DIR.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
