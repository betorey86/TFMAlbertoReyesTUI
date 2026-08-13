"""
Extracción por lotes de las capas temáticas de OSM para toda España.

Recorre restauración, atracciones y transporte por comunidad autónoma, de una en una y con
pausa entre consultas. Empieza por las seis CCAA que tienen registro oficial de VUT, que son
donde más aporta cruzar todas las capas con la oferta de alojamiento.

Es reanudable: cada unidad (capa × CCAA) se guarda en su propio JSON y queda anotada en
data/raw/capas_progreso.json. Al relanzar, lo ya hecho se salta.

Uso:
    python etl/extract/extract_osm_capas_batch.py
    python etl/extract/extract_osm_capas_batch.py --solo-prioritarias
    python etl/extract/extract_osm_capas_batch.py --capas restauracion --pausa 30
    python etl/extract/extract_osm_capas_batch.py --rehacer          # ignora lo ya extraído
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _capa_osm import Capa
from extract_osm import (
    CCAA,
    PROJECT_ROOT,
    RAW_DIR,
    construir_query_filtros,
    consultar_overpass,
    guardar_raw,
    resumen_por_clave,
)
from extract_osm_atracciones import CAPA as CAPA_ATRACCIONES
from extract_osm_camping import FILTROS as FILTROS_CAMPING
from extract_osm_camping import normalizar as normalizar_camping
from extract_osm_restauracion import CAPA as CAPA_RESTAURACION
from extract_osm_transporte import CAPA as CAPA_TRANSPORTE

PAUSA_ENTRE_CONSULTAS = 25
PROGRESO = RAW_DIR / "capas_progreso.json"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

CAPA_CAMPING = Capa(
    prefijo="camping",
    descripcion="Campings y áreas de autocaravana.",
    perfiles={"estandar": FILTROS_CAMPING},
    perfil_por_defecto="estandar",
    claves_resumen=("tourism",),
)

CAPAS: dict[str, tuple[Capa, str]] = {
    "restauracion": (CAPA_RESTAURACION, "estandar"),
    "atracciones": (CAPA_ATRACCIONES, "estandar"),
    "transporte": (CAPA_TRANSPORTE, "principales"),
    "camping": (CAPA_CAMPING, "estandar"),
}

# Camping va al final: es la capa más reciente y la de menor volumen, así que no debe
# retrasar la extracción de las que ya estaban en marcha. Con esto, una ejecución completa
# termina primero las tres capas originales en todas las CCAA y sólo después empieza
# con camping.
CAPAS_BAJA_PRIORIDAD = ("camping",)

# Capas que además del crudo generan un CSV normalizado en data/processed/.
NORMALIZADORES = {"camping": normalizar_camping}

# Capas tan dispersas que un resultado de 0 puede ser real: Melilla tiene 13 km² y es
# perfectamente posible que no haya ningún camping. El guardia contra respuestas
# degradadas se mantiene —se agotan réplicas y reintentos— y sólo si el 0 se repite de
# forma consistente se acepta como dato. Una respuesta degradada es transitoria y no
# reproducible; un 0 real sí lo es.
CAPAS_PUEDEN_ESTAR_VACIAS = ("camping",)

# Las seis con registro oficial de VUT: son las que permiten contrastar la oferta de OSM
# con la realidad administrativa, así que van primero.
PRIORITARIAS = ["baleares", "canarias", "cataluna", "andalucia", "madrid", "valencia"]

# El resto, de menor a mayor volumen esperado.
RESTO = [
    "ceuta", "melilla", "la-rioja", "cantabria", "navarra", "asturias", "murcia",
    "extremadura", "aragon", "pais-vasco", "castilla-la-mancha", "galicia",
    "castilla-y-leon",
]


def nombre_capa(slug_capa: str) -> str:
    capa, perfil = CAPAS[slug_capa]
    return f"{capa.prefijo}_{perfil}" if len(capa.perfiles) > 1 else capa.prefijo


def cargar_progreso() -> dict:
    if PROGRESO.exists():
        try:
            return json.loads(PROGRESO.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"unidades": {}}


def guardar_progreso(progreso: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    progreso["actualizado"] = datetime.now(timezone.utc).isoformat()
    PROGRESO.write_text(json.dumps(progreso, ensure_ascii=False, indent=2), encoding="utf-8")


def fichero_de(slug_capa: str, slug_ccaa: str) -> Path | None:
    """Extracción previa de esa capa y CCAA, si existe (cualquier fecha)."""
    encontrados = sorted(RAW_DIR.glob(f"osm_{nombre_capa(slug_capa)}_{slug_ccaa}_*.json"))
    return encontrados[-1] if encontrados else None


def formatear_duracion(segundos: float) -> str:
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else (f"{m}m {s}s" if m else f"{s}s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae las capas temáticas de OSM para toda España, CCAA a CCAA."
    )
    parser.add_argument("--capas", nargs="+", choices=sorted(CAPAS), default=sorted(CAPAS))
    parser.add_argument("--ccaa", nargs="+", help="Subconjunto de CCAA.")
    parser.add_argument("--solo-prioritarias", action="store_true",
                        help="Sólo las 6 CCAA con registro oficial de VUT.")
    parser.add_argument("--pausa", type=int, default=PAUSA_ENTRE_CONSULTAS)
    parser.add_argument("--rehacer", action="store_true",
                        help="Vuelve a extraer aunque ya exista el fichero.")
    args = parser.parse_args()

    if args.ccaa:
        desconocidas = [c for c in args.ccaa if c.lower() not in CCAA]
        if desconocidas:
            print(f"CCAA desconocidas: {', '.join(desconocidas)}", file=sys.stderr)
            return 1
        objetivo_ccaa = [c.lower() for c in args.ccaa]
    else:
        objetivo_ccaa = PRIORITARIAS if args.solo_prioritarias else PRIORITARIAS + RESTO

    # CCAA en el bucle externo: así las prioritarias quedan completas cuanto antes. Las
    # capas de baja prioridad se dejan para el final del todo, para no retrasar a las demás.
    normales = [k for k in args.capas if k not in CAPAS_BAJA_PRIORIDAD]
    tardias = [k for k in args.capas if k in CAPAS_BAJA_PRIORIDAD]
    unidades = (
        [(c, k) for c in objetivo_ccaa for k in normales]
        + [(c, k) for k in tardias for c in objetivo_ccaa]
    )

    progreso = cargar_progreso()
    inicio = time.time()
    hechas = saltadas = fallidas = 0

    print(f"Capas: {', '.join(args.capas)}")
    print(f"CCAA: {len(objetivo_ccaa)}  ->  {len(unidades)} unidades")
    print(f"Pausa entre consultas: {args.pausa}s\n")

    for i, (slug_ccaa, slug_capa) in enumerate(unidades, start=1):
        iso, nombre = CCAA[slug_ccaa]
        capa, perfil = CAPAS[slug_capa]
        clave = f"{slug_capa}/{slug_ccaa}"
        etiqueta = f"[{i}/{len(unidades)}] {slug_capa} — {nombre}"

        if not args.rehacer:
            previo = fichero_de(slug_capa, slug_ccaa)
            if previo:
                # Se recupera el recuento del fichero para que el resumen final refleje
                # también las unidades reutilizadas, no sólo las de esta ejecución.
                try:
                    meta = json.loads(previo.read_text(encoding="utf-8"))["metadata"]
                    progreso["unidades"][clave] = {
                        "estado": "ok",
                        "elementos": meta["num_elementos"],
                        "fichero": previo.name,
                        "resumen": meta.get("resumen_por_tipo", {}),
                        "fecha": meta.get("fecha_extraccion"),
                        "reutilizado": True,
                    }
                    guardar_progreso(progreso)
                except (ValueError, KeyError, OSError):
                    print(f"  {previo.name} ilegible: se vuelve a extraer.")
                else:
                    print(f"{etiqueta}: ya extraído ({previo.name}), se salta")
                    saltadas += 1
                    continue

        print(f"{etiqueta} ({iso})")
        t0 = time.time()
        query = construir_query_filtros(iso, capa.perfiles[perfil])
        vacio_confirmado = False
        try:
            datos = consultar_overpass(query)
        except RuntimeError as exc:
            # Si la capa admite el vacío y el fallo fue precisamente por 0 elementos,
            # se hace una última consulta aceptándolo: el 0 ya se ha repetido en todas
            # las réplicas y reintentos, así que es un dato, no un corte del servicio.
            reintentar_vacio = (
                slug_capa in CAPAS_PUEDEN_ESTAR_VACIAS and "0 elementos" in str(exc)
            )
            if not reintentar_vacio:
                print(f"  FALLO: {exc}\n")
                progreso["unidades"][clave] = {"estado": "error", "error": str(exc)[:300]}
                guardar_progreso(progreso)
                fallidas += 1
                continue

            print("  0 elementos de forma consistente: se acepta como territorio sin oferta.")
            try:
                datos = consultar_overpass(query, reintentos=1, permitir_vacio=True)
            except RuntimeError as exc2:
                print(f"  FALLO: {exc2}\n")
                progreso["unidades"][clave] = {"estado": "error", "error": str(exc2)[:300]}
                guardar_progreso(progreso)
                fallidas += 1
                continue
            vacio_confirmado = True

        elementos = datos.get("elements", [])
        fichero = guardar_raw(
            datos, slug_ccaa, iso, nombre, capa.perfiles[perfil],
            prefijo=nombre_capa(slug_capa), claves_resumen=capa.claves_resumen,
        )

        # Algunas capas, además del crudo, producen su CSV en el esquema común de oferta.
        fichero_norm = None
        normalizador = NORMALIZADORES.get(slug_capa)
        if normalizador is not None:
            PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
            df = normalizador(elementos, nombre, datetime.now(timezone.utc))
            fichero_norm = PROCESSED_DIR / f"{slug_capa}_normalizado_{slug_ccaa}.csv"
            df.to_csv(fichero_norm, index=False, encoding="utf-8")

        progreso["unidades"][clave] = {
            "estado": "ok",
            "elementos": len(elementos),
            "fichero": fichero.name,
            "fichero_normalizado": fichero_norm.name if fichero_norm else None,
            "resumen": resumen_por_clave(elementos, capa.claves_resumen),
            "vacio_confirmado": vacio_confirmado or None,
            "fecha": datetime.now(timezone.utc).isoformat(),
        }
        guardar_progreso(progreso)
        hechas += 1

        print(f"  {len(elementos):,} elementos en {formatear_duracion(time.time() - t0)}")
        print(f"  -> {fichero.name}")

        if i < len(unidades):
            print(f"  Pausa {args.pausa}s...\n")
            time.sleep(args.pausa)
        else:
            print()

    # ---------------- Resumen ----------------
    duracion = time.time() - inicio
    print("\n" + "=" * 84)
    print("RESUMEN — CAPAS TEMÁTICAS OSM")
    print("=" * 84)

    cab = f"\n{'Comunidad autónoma':<26}" + "".join(f"{k:>16}" for k in args.capas)
    print(cab)
    print("-" * len(cab.strip("\n")))

    totales = {k: 0 for k in args.capas}
    for slug_ccaa in objetivo_ccaa:
        linea = f"{CCAA[slug_ccaa][1]:<26}"
        for k in args.capas:
            u = progreso["unidades"].get(f"{k}/{slug_ccaa}", {})
            if u.get("estado") == "ok":
                linea += f"{u['elementos']:>16,}"
                totales[k] += u["elementos"]
            else:
                linea += f"{'-':>16}"
        print(linea)

    print("-" * len(cab.strip("\n")))
    print(f"{'TOTAL':<26}" + "".join(f"{totales[k]:>16,}" for k in args.capas))

    print(f"\nExtraídas: {hechas} | Reutilizadas: {saltadas} | Fallidas: {fallidas}")
    print(f"Duración: {formatear_duracion(duracion)}")

    errores = {c: u for c, u in progreso["unidades"].items() if u.get("estado") == "error"}
    if errores:
        print(f"\nCON ERRORES ({len(errores)}):")
        for clave, u in errores.items():
            print(f"  - {clave}: {u['error'][:110]}")
        print("\n  Relanza el script: las unidades correctas se saltan y sólo se reintentan éstas.")

    print(f"\nProgreso: {PROGRESO.relative_to(PROJECT_ROOT)}")
    return 0 if not errores else 3


if __name__ == "__main__":
    raise SystemExit(main())
