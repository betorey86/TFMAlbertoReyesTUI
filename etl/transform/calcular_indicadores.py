"""
Indicadores territoriales a partir de los agregados municipales.

Añade a `agregados_municipales.csv` los indicadores normalizados y escribe
`data/processed/indicadores_municipales.csv`.

Indicadores:

  saturacion_plazas_1000hab   plazas de VUT por cada 1.000 habitantes
  densidad_plazas_km2         plazas de VUT por km²
  densidad_servicios_km2      (restauración + atracciones) por km²
  dist_transporte_km          distancia al nodo de transporte más cercano
  indice_oportunidad          demanda alta con saturación baja
  indice_riesgo               saturación alta con presión de servicios

Regla que atraviesa todo el script: **donde `origen_vut` es `sin_dato` no se calcula nada
que dependa de las VUT**. Esos municipios quedan como no disponible, nunca como cero. Un 0
en saturación se leería como "aquí no hay presión turística" cuando lo que ocurre es que esa
comunidad no publica registro; es la confusión que más daño haría a las conclusiones.

Uso:
    python etl/transform/calcular_indicadores.py
    python etl/transform/calcular_indicadores.py --poblacion-minima 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

ENTRADA = PROCESSED_DIR / "agregados_municipales.csv"
SALIDA = PROCESSED_DIR / "indicadores_municipales.csv"

RADIO_TIERRA_KM = 6371.0

# Municipios muy pequeños disparan cualquier ratio por habitante: 20 plazas en un pueblo de
# 30 vecinos da 666 por 1.000, y eso no es una señal de saturación turística sino de
# denominador pequeño. Se calculan igual, pero los rankings se filtran por este umbral.
POBLACION_MINIMA_RANKING = 1_000


# ---------------------------------------------------------------------------
# Distancia al transporte
# ---------------------------------------------------------------------------

def cargar_puntos_transporte() -> pd.DataFrame:
    """Nodos de entrada al destino: aeropuertos, ferris, estaciones e intercambiadores."""
    import glob
    import json

    filas = []
    ficheros = [Path(f) for f in glob.glob(str(RAW_DIR / "osm_transporte_principales_*.json"))]
    por_ccaa: dict[str, Path] = {}
    for f in ficheros:
        slug = f.stem.split("_")[-2]
        if slug not in por_ccaa or f.name > por_ccaa[slug].name:
            por_ccaa[slug] = f

    for fichero in por_ccaa.values():
        try:
            with fichero.open(encoding="utf-8") as fh:
                datos = json.load(fh)
        except (ValueError, OSError):
            continue
        for el in datos.get("osm", {}).get("elements", []):
            centro = el.get("center") or {}
            lat = el.get("lat", centro.get("lat"))
            lon = el.get("lon", centro.get("lon"))
            if lat is None or lon is None:
                continue
            tags = el.get("tags", {})
            tipo = next((tags[c] for c in ("aeroway", "railway", "amenity", "public_transport")
                         if c in tags), "desconocido")
            filas.append({"lat": lat, "lon": lon, "tipo": tipo})
    return pd.DataFrame(filas)


def distancia_minima_km(lat_a, lon_a, lat_b, lon_b, bloque: int = 500) -> np.ndarray:
    """
    Distancia haversine de cada punto A al punto B más cercano.

    Se calcula sobre la esfera y no con una proyección plana porque el territorio incluye
    Canarias: a 1.700 km de la península, cualquier UTM peninsular deformaría la distancia
    lo bastante como para alterar el orden de los vecinos.
    """
    lat_a = np.radians(np.asarray(lat_a, dtype=float))
    lon_a = np.radians(np.asarray(lon_a, dtype=float))
    lat_b = np.radians(np.asarray(lat_b, dtype=float))
    lon_b = np.radians(np.asarray(lon_b, dtype=float))

    resultado = np.empty(len(lat_a), dtype=float)
    for i in range(0, len(lat_a), bloque):
        la = lat_a[i:i + bloque][:, None]
        lo = lon_a[i:i + bloque][:, None]
        dlat = lat_b[None, :] - la
        dlon = lon_b[None, :] - lo
        h = np.sin(dlat / 2) ** 2 + np.cos(la) * np.cos(lat_b[None, :]) * np.sin(dlon / 2) ** 2
        d = 2 * RADIO_TIERRA_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
        resultado[i:i + bloque] = d.min(axis=1)
    return resultado


# ---------------------------------------------------------------------------
# Índices compuestos
# ---------------------------------------------------------------------------

def rango_percentil(serie: pd.Series) -> pd.Series:
    """
    Posición relativa de cada municipio en la distribución, de 0 a 100.

    Se usa el percentil y no el valor normalizado porque estos indicadores tienen colas
    muy largas: Benahavís tiene 1.478 plazas por 1.000 habitantes y la mediana está cerca
    de cero. Con una normalización lineal, el 99 % de los municipios quedaría aplastado
    contra el 0 y el índice no distinguiría nada.
    """
    return serie.rank(pct=True, na_option="keep") * 100


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calcula los indicadores territoriales por municipio."
    )
    parser.add_argument("--poblacion-minima", type=int, default=POBLACION_MINIMA_RANKING,
                        help="Población mínima para aparecer en los rankings.")
    args = parser.parse_args()

    if not ENTRADA.exists():
        print(f"ERROR: falta {ENTRADA}", file=sys.stderr)
        print("Ejecuta antes: python etl/transform/agregar_municipal.py", file=sys.stderr)
        return 1

    df = pd.read_csv(ENTRADA, dtype={"codigo_ine": str})
    df["codigo_ine"] = df["codigo_ine"].str.zfill(5)
    print(f"Municipios: {len(df):,}".replace(",", "."))

    hay_vut = df["origen_vut"] != "sin_dato"
    print(f"  Con dato de VUT: {int(hay_vut.sum()):,}".replace(",", ".")
          + f"  |  sin dato: {int((~hay_vut).sum()):,}".replace(",", "."))

    # --- Indicadores de saturación y densidad (sólo donde hay dato de VUT) ---
    plazas = pd.to_numeric(df["plazas_vut"], errors="coerce").where(hay_vut)
    poblacion = pd.to_numeric(df["poblacion"], errors="coerce")
    superficie = pd.to_numeric(df["superficie_km2"], errors="coerce")

    df["saturacion_plazas_1000hab"] = (
        1000 * plazas / poblacion.replace(0, np.nan)
    ).round(2)
    df["densidad_plazas_km2"] = (plazas / superficie.replace(0, np.nan)).round(3)

    # --- Saturación total: VUT + hoteles ---
    #
    # Sólo donde ambos datos son fiables: registro de VUT y punto turístico de la EOH. En
    # el resto no se suma un cero hotelero, porque no sabemos que sea cero: sabemos que no
    # tenemos el dato municipal. Sumarlo daría una saturación total menor que la de sólo
    # VUT, que sería absurdo.
    hotelero_fiable = df.get("origen_hotelero", pd.Series("sin_dato", index=df.index)) \
        == "eoh_punto_turistico"
    plazas_hot = pd.to_numeric(df.get("plazas_hoteleras"), errors="coerce")

    df["plazas_totales"] = (plazas + plazas_hot).where(hay_vut & hotelero_fiable)
    df["saturacion_total_1000hab"] = (
        1000 * df["plazas_totales"] / poblacion.replace(0, np.nan)
    ).round(2)
    df["pct_plazas_hoteleras"] = (
        100 * plazas_hot / df["plazas_totales"].replace(0, np.nan)
    ).round(1)

    # --- Servicios: OSM cubre todo el territorio, así que se calcula en todos ---
    servicios = df["n_restauracion"].fillna(0) + df["n_atracciones"].fillna(0)
    df["n_servicios"] = servicios.astype(int)
    df["densidad_servicios_km2"] = (servicios / superficie.replace(0, np.nan)).round(3)
    df["servicios_1000hab"] = (1000 * servicios / poblacion.replace(0, np.nan)).round(2)

    # --- Accesibilidad ---
    print("\nCalculando distancia al nodo de transporte más cercano…")
    transporte = cargar_puntos_transporte()
    if transporte.empty:
        print("  AVISO: sin capa de transporte; la accesibilidad queda vacía.")
        df["dist_transporte_km"] = np.nan
    else:
        print(f"  Nodos de transporte: {len(transporte):,}".replace(",", "."))
        df["dist_transporte_km"] = np.round(
            distancia_minima_km(df["lat_centro"], df["lon_centro"],
                                transporte["lat"], transporte["lon"]), 2
        )

    # --- Índices compuestos ---
    # Demanda potencial: recursos y servicios que atraen visitantes.
    #
    # El peso principal va a los servicios **por habitante**, no por km². La densidad por
    # km² mide urbanidad, no turismo: un barrio dormitorio del área metropolitana de
    # Valencia tiene muchos bares por km² y ninguno es turístico. Los servicios por
    # habitante sí discriminan: Comillas tiene 27 por cada 1.000 vecinos y Cadaqués 31,
    # frente a 0,5 de Catarroja. Un pueblo con más hostelería de la que su población
    # justifica está atendiendo a visitantes.
    df["atracciones_1000hab"] = (
        1000 * df["n_atracciones"] / poblacion.replace(0, np.nan)
    ).round(2)

    p_serv_hab = rango_percentil(df["servicios_1000hab"])
    p_atrac_hab = rango_percentil(df["atracciones_1000hab"])
    p_serv_km2 = rango_percentil(df["densidad_servicios_km2"])
    # La accesibilidad se invierte: cuanta menos distancia, más accesible. Pesa poco a
    # propósito: con un peso alto, cualquier municipio de área metropolitana encabeza el
    # índice sólo por tener una estación de cercanías al lado.
    p_acceso = 100 - rango_percentil(df["dist_transporte_km"])

    df["indice_demanda"] = (
        0.40 * p_serv_hab + 0.25 * p_atrac_hab + 0.20 * p_serv_km2 + 0.15 * p_acceso
    ).round(1)

    # Saturación de oferta: sólo donde hay registro de VUT.
    p_saturacion = rango_percentil(df["saturacion_plazas_1000hab"])
    p_densidad = rango_percentil(df["densidad_plazas_km2"])
    df["indice_saturacion"] = (0.60 * p_saturacion + 0.40 * p_densidad).round(1)

    # Oportunidad: demanda alta con saturación baja. Riesgo: lo contrario.
    # Ambos requieren saber la saturación, así que quedan vacíos donde no hay dato de VUT.
    df["indice_oportunidad"] = (df["indice_demanda"] - df["indice_saturacion"]).round(1)
    df["indice_riesgo"] = (
        0.65 * df["indice_saturacion"] + 0.35 * df["indice_demanda"]
    ).round(1)

    # Los índices que dependen de la saturación no se calculan donde no hay registro ni
    # donde el dato mide otra magnitud. Madrid publica licencias urbanísticas, no
    # inscripciones: dejarlo dentro lo colocaría entre las "oportunidades de inversión"
    # por tener una saturación aparentemente baja.
    comparable = df.get("cobertura_vut", pd.Series("registro", index=df.index)) != "no_comparable"
    excluidos = ~hay_vut | ~comparable
    df.loc[excluidos, ["indice_saturacion", "indice_oportunidad", "indice_riesgo",
                       "saturacion_plazas_1000hab", "densidad_plazas_km2"]] = np.nan
    n_no_comp = int((hay_vut & ~comparable).sum())
    if n_no_comp:
        print(f"  Excluidos por medir otra magnitud (licencias, no registro): {n_no_comp}")

    df.to_csv(SALIDA, index=False, encoding="utf-8")

    # ---------------- Validación ----------------
    grande = df["poblacion"] >= args.poblacion_minima
    print(f"\nRankings sobre municipios de {args.poblacion_minima:,}+ habitantes "
          .replace(",", ".") + f"({int(grande.sum()):,} de {len(df):,})".replace(",", "."))

    def ranking(columna: str, titulo: str, unidad: str, requiere_vut: bool = True) -> None:
        base = df[grande & df[columna].notna()]
        if requiere_vut:
            base = base[base["origen_vut"] != "sin_dato"]
        if base.empty:
            print(f"\n  {titulo}: sin datos suficientes")
            return

        print("\n" + "=" * 78)
        print(f"  {titulo}  ({unidad})   n = {len(base):,}".replace(",", "."))
        print("=" * 78)

        def linea(f) -> str:
            return (f"    {f['nombre'][:26]:<26} {f['provincia'][:16]:<16} "
                    f"{int(f['poblacion']):>8,} hab  {f[columna]:>10,.2f}".replace(",", "."))

        print("  TOP 10")
        for _, f in base.nlargest(10, columna).iterrows():
            print(linea(f))

        # En los indicadores con muchos ceros, un "bottom 10" son diez empates elegidos por
        # orden de fichero: no informa de nada. Se reporta cuántos hay en cero y el fondo
        # se toma entre los que sí tienen algo.
        ceros = int((base[columna] == 0).sum())
        if ceros >= 10:
            print(f"  BOTTOM 10  ({ceros:,} municipios están en 0; ".replace(",", ".")
                  + "el fondo se toma entre los que tienen valor > 0)")
            base = base[base[columna] > 0]
            if base.empty:
                return
        else:
            print("  BOTTOM 10")
        for _, f in base.nsmallest(10, columna).iterrows():
            print(linea(f))

    ranking("saturacion_plazas_1000hab", "SATURACIÓN", "plazas de VUT por 1.000 hab")
    ranking("densidad_plazas_km2", "DENSIDAD DE OFERTA", "plazas de VUT por km²")
    ranking("densidad_servicios_km2", "DENSIDAD DE SERVICIOS",
            "restauración + atracciones por km²", requiere_vut=False)
    ranking("dist_transporte_km", "ACCESIBILIDAD",
            "km al nodo de transporte más cercano", requiere_vut=False)
    ranking("indice_oportunidad", "ÍNDICE DE OPORTUNIDAD", "demanda − saturación")
    ranking("indice_riesgo", "ÍNDICE DE RIESGO", "saturación + presión de servicios")

    # --- Efecto de incorporar la capa hotelera ---
    comp = df[grande & df["saturacion_total_1000hab"].notna()].copy()
    if not comp.empty:
        comp["puesto_vut"] = comp["saturacion_plazas_1000hab"].rank(ascending=False)
        comp["puesto_total"] = comp["saturacion_total_1000hab"].rank(ascending=False)
        comp["salto"] = comp["puesto_vut"] - comp["puesto_total"]

        print("\n" + "=" * 88)
        print(f"  EFECTO DE INCLUIR HOTELES   ({len(comp):,} municipios con ambos datos)"
              .replace(",", "."))
        print("=" * 88)
        print(f"    {'Municipio':<24}{'sólo VUT':>12}{'VUT+hotel':>12}"
              f"{'% hotel':>9}{'puesto VUT':>12}{'puesto tot':>12}")
        print("    " + "-" * 81)
        for _, f in comp.nlargest(15, "saturacion_total_1000hab").iterrows():
            print(f"    {f['nombre'][:23]:<24}{f['saturacion_plazas_1000hab']:>12,.0f}"
                  f"{f['saturacion_total_1000hab']:>12,.0f}{f['pct_plazas_hoteleras']:>8.0f}%"
                  f"{int(f['puesto_vut']):>12}{int(f['puesto_total']):>12}".replace(",", "."))

        print("\n    Los que más suben al contar hoteles (destinos hoteleros que el "
              "índice de sólo VUT infravaloraba):")
        for _, f in comp.nlargest(10, "salto").iterrows():
            print(f"    {f['nombre'][:23]:<24}{f['saturacion_plazas_1000hab']:>12,.0f}"
                  f"{f['saturacion_total_1000hab']:>12,.0f}{f['pct_plazas_hoteleras']:>8.0f}%"
                  f"{int(f['puesto_vut']):>12}{int(f['puesto_total']):>12}".replace(",", "."))

    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
