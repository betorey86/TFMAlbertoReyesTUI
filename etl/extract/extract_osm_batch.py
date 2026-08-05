"""
Extracción masiva de alojamientos turísticos de OSM para toda España.

Recorre las 17 comunidades autónomas + Ceuta y Melilla usando sus códigos ISO 3166-2,
guarda un JSON por CCAA en data/raw/ (mismo formato que extract_osm.py) y al terminar
genera un fichero consolidado con todo junto.

Reutiliza la consulta, la rotación entre réplicas de Overpass y los reintentos de
extract_osm.py, y añade una pausa entre comunidades para no saturar la API.

Uso:
    python etl/extract/extract_osm_batch.py
    python etl/extract/extract_osm_batch.py --pausa 30
    python etl/extract/extract_osm_batch.py --saltar-existentes
    python etl/extract/extract_osm_batch.py --ccaa madrid cataluna galicia
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Permite ejecutar el script directamente (python etl/extract/extract_osm_batch.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_osm import (
    CCAA,
    PROJECT_ROOT,
    RAW_DIR,
    TIPOS_ALOJAMIENTO,
    construir_query,
    consultar_overpass,
    guardar_raw,
    resumen_por_tipo,
)

PAUSA_ENTRE_CCAA = 20  # segundos; Overpass penaliza las ráfagas de consultas

# Orden de extracción: las comunidades pequeñas primero. Si algo va mal con la API,
# los fallos aparecen pronto y sin haber gastado las consultas caras.
ORDEN_EXTRACCION = [
    "ceuta", "melilla", "la-rioja", "cantabria", "navarra", "asturias",
    "murcia", "extremadura", "baleares", "aragon", "pais-vasco",
    "castilla-la-mancha", "madrid", "galicia", "canarias",
    "castilla-y-leon", "valencia", "cataluna", "andalucia",
]


def formatear_duracion(segundos: float) -> str:
    minutos, seg = divmod(int(segundos), 60)
    if minutos:
        return f"{minutos}m {seg}s"
    return f"{seg}s"


def fichero_existente(slug: str) -> Path | None:
    """Busca una extracción previa de esa CCAA (cualquier fecha)."""
    encontrados = sorted(RAW_DIR.glob(f"osm_alojamientos_{slug}_*.json"))
    return encontrados[-1] if encontrados else None


def cargar_elementos(fichero: Path) -> list[dict]:
    with fichero.open(encoding="utf-8") as f:
        return json.load(f).get("osm", {}).get("elements", [])


def guardar_consolidado(
    por_ccaa: dict[str, list[dict]], tipos: list[str], inicio: float
) -> Path:
    """Une todas las CCAA en un único fichero, etiquetando cada elemento con su CCAA."""
    ahora = datetime.now(timezone.utc)

    elementos = []
    for slug, els in por_ccaa.items():
        iso, nombre = CCAA[slug]
        for el in els:
            # Copia superficial para no mutar lo ya guardado por CCAA.
            elementos.append({**el, "ccaa_slug": slug, "ccaa_nombre": nombre, "ccaa_iso": iso})

    salida = {
        "metadata": {
            "ambito": "España (todas las CCAA extraídas)",
            "fuente_dato": "openstreetmap-overpass",
            "tipos_consultados": tipos,
            "fecha_extraccion": ahora.isoformat(),
            "duracion_segundos": round(time.time() - inicio, 1),
            "ccaa_incluidas": sorted(por_ccaa),
            "num_ccaa": len(por_ccaa),
            "num_elementos": len(elementos),
            "resumen_por_tipo": resumen_por_tipo(elementos),
            "resumen_por_ccaa": {
                slug: len(els) for slug, els in sorted(por_ccaa.items())
            },
        },
        "elements": elementos,
    }

    fichero = RAW_DIR / f"osm_alojamientos_espana_consolidado_{ahora:%Y%m%d}.json"
    with fichero.open("w", encoding="utf-8") as f:
        # Sin indentación: el consolidado es un artefacto intermedio para el ETL,
        # no está pensado para leerse a mano, y así ocupa la mitad.
        json.dump(salida, f, ensure_ascii=False)

    return fichero


def imprimir_resumen(
    por_ccaa: dict[str, list[dict]],
    fallos: dict[str, str],
    tipos: list[str],
    duracion: float,
) -> None:
    print("\n" + "=" * 78)
    print("RESUMEN DE LA EXTRACCIÓN — ESPAÑA")
    print("=" * 78)

    cols = list(tipos)
    ancho_nombre = 26

    cabecera = f"{'Comunidad autónoma':<{ancho_nombre}}" + "".join(f"{c:>13}" for c in cols) + f"{'TOTAL':>10}"
    print("\n" + cabecera)
    print("-" * len(cabecera))

    totales = {c: 0 for c in cols}
    total_general = 0

    # Ordenado por volumen: es la lectura útil para decidir por dónde seguir.
    for slug in sorted(por_ccaa, key=lambda s: len(por_ccaa[s]), reverse=True):
        conteo = resumen_por_tipo(por_ccaa[slug])
        fila_total = len(por_ccaa[slug])
        total_general += fila_total
        linea = f"{CCAA[slug][1]:<{ancho_nombre}}"
        for c in cols:
            n = conteo.get(c, 0)
            totales[c] += n
            linea += f"{n:>13,}"
        linea += f"{fila_total:>10,}"
        print(linea)

    print("-" * len(cabecera))
    linea = f"{'TOTAL':<{ancho_nombre}}"
    for c in cols:
        linea += f"{totales[c]:>13,}"
    linea += f"{total_general:>10,}"
    print(linea)

    print(f"\nCCAA extraídas: {len(por_ccaa)}/{len(por_ccaa) + len(fallos)}")
    print(f"Duración total: {formatear_duracion(duracion)}")

    if fallos:
        print(f"\nCCAA CON ERRORES ({len(fallos)}):")
        for slug, motivo in fallos.items():
            print(f"  - {CCAA[slug][1]}: {motivo}")
        print("\n  Relánzalo con --saltar-existentes para reintentar sólo las que faltan.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae alojamientos turísticos de OSM para todas las CCAA de España."
    )
    parser.add_argument(
        "--ccaa", nargs="+",
        help="Subconjunto de CCAA a extraer (por defecto: las 19).",
    )
    parser.add_argument(
        "--tipos", nargs="+", default=TIPOS_ALOJAMIENTO,
        help=f"Valores de tourism=* (por defecto: {' '.join(TIPOS_ALOJAMIENTO)})",
    )
    parser.add_argument(
        "--pausa", type=int, default=PAUSA_ENTRE_CCAA,
        help=f"Segundos de espera entre CCAA (por defecto: {PAUSA_ENTRE_CCAA}).",
    )
    parser.add_argument(
        "--saltar-existentes", action="store_true",
        help="Reutiliza los JSON ya descargados en data/raw/ en vez de volver a pedirlos.",
    )
    args = parser.parse_args()

    if args.ccaa:
        desconocidas = [c for c in args.ccaa if c.lower() not in CCAA]
        if desconocidas:
            print(f"CCAA desconocidas: {', '.join(desconocidas)}", file=sys.stderr)
            print(f"Opciones: {', '.join(sorted(CCAA))}", file=sys.stderr)
            return 1
        objetivo = [c.lower() for c in args.ccaa]
    else:
        objetivo = ORDEN_EXTRACCION

    inicio = time.time()
    por_ccaa: dict[str, list[dict]] = {}
    fallos: dict[str, str] = {}

    print(f"Extracción de alojamientos OSM — {len(objetivo)} comunidades")
    print(f"Tipos: {', '.join(args.tipos)}")
    print(f"Pausa entre CCAA: {args.pausa}s\n")

    for i, slug in enumerate(objetivo, start=1):
        iso, nombre = CCAA[slug]
        print(f"[{i}/{len(objetivo)}] {nombre} ({iso})")

        if args.saltar_existentes:
            previo = fichero_existente(slug)
            if previo:
                elementos = cargar_elementos(previo)
                # Un fichero de 0 elementos es una extracción degradada de una ejecución
                # anterior, no un dato real: se vuelve a pedir.
                if elementos:
                    por_ccaa[slug] = elementos
                    print(f"  Reutilizando {previo.name}: {len(elementos)} elementos\n")
                    continue
                print(f"  {previo.name} tiene 0 elementos: se descarta y se vuelve a extraer.")

        t0 = time.time()
        try:
            datos = consultar_overpass(construir_query(iso, args.tipos))
        except RuntimeError as exc:
            print(f"  FALLO: {exc}\n")
            fallos[slug] = str(exc)
            continue

        elementos = datos.get("elements", [])
        por_ccaa[slug] = elementos
        fichero = guardar_raw(datos, slug, iso, nombre, args.tipos)

        print(f"  {len(elementos):,} elementos en {formatear_duracion(time.time() - t0)}")
        print(f"  -> {fichero.name}")

        # Sin pausa tras la última: no hay nada más que proteger.
        if i < len(objetivo):
            print(f"  Pausa {args.pausa}s...\n")
            time.sleep(args.pausa)
        else:
            print()

    duracion = time.time() - inicio

    if not por_ccaa:
        print("No se extrajo ninguna CCAA. No se genera consolidado.", file=sys.stderr)
        imprimir_resumen(por_ccaa, fallos, args.tipos, duracion)
        return 2

    consolidado = guardar_consolidado(por_ccaa, args.tipos, inicio)

    imprimir_resumen(por_ccaa, fallos, args.tipos, duracion)
    tam_mb = consolidado.stat().st_size / (1024 * 1024)
    print(f"\nConsolidado: {consolidado.relative_to(PROJECT_ROOT)} ({tam_mb:.1f} MB)")

    return 0 if not fallos else 3


if __name__ == "__main__":
    raise SystemExit(main())
