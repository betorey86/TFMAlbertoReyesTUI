"""
Agregación municipal de las VUT de Galicia.

Galicia se trata a **resolución municipal**, no de punto. La decisión no es de comodidad:
se probaron los dos geocodificadores disponibles sobre la misma muestra de 300 direcciones
y ninguno llega a un umbral aceptable (Catastro 45,0 %, Cartociudad 26,7 %, combinados
57,0 %). Y lo que falla no es aleatorio: son los topónimos rurales dispersos
("LUGAR DE PEREIRIÑA", "LG. SEÑORANS S/N"), de modo que geocodificar dejaría el mapa con
Vigo y A Coruña y sin el rural gallego. Un mapa así diría "aquí no hay presión turística"
donde en realidad dice "aquí no supimos ubicar la oferta".

A nivel de concello, en cambio, el dato es sólido: el municipio está en el 100 % de los
registros y las plazas en el 98,8 %, que es justo lo que necesita el ratio de saturación.

Salida:
    data/processed/vut_galicia_municipal.csv

Uso:
    python etl/transform/agregar_galicia_municipal.py
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

ENTRADA = PROCESSED_DIR / "vut_normalizado_galicia.csv"
SALIDA = PROCESSED_DIR / "vut_galicia_municipal.csv"

# Artículos que van pospuestos en unas fuentes y antepuestos en otras. El registro escribe
# "A CORUÑA" y el INE "Coruña, A": sin quitarlos, el cruce por nombre falla justo en los
# concellos más grandes.
ARTICULOS = {"a", "o", "as", "os", "el", "la", "los", "las", "lo"}

ENTRADA_MUNICIPIOS = PROCESSED_DIR / "municipios_ine.csv"

# Concellos que el REAT nombra con una denominación alternativa o histórica y que por eso
# no casan con el INE ni normalizando el nombre. Los códigos están verificados contra
# `municipios_ine.csv`. Son sólo tres, pero uno de ellos —Cangas— concentra 651 VUT en la
# costa de las Rías Baixas, justo el perfil que más pesa en el análisis de saturación:
# dejarlo sin cruzar lo borraría del mapa sin que nada fallara.
EQUIVALENCIAS_INE = {
    "CANGAS DE MORRAZO": "36008",    # INE: Cangas (Pontevedra)
    "O CASTRO DE CALDELAS": "32023",  # INE: Castro Caldelas (Ourense)
    "ALFOZ DO CASTRODOURO": "27002",  # INE: Alfoz (Lugo)
}


def normalizar(texto: object) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def clave_join(municipio: object, provincia: object) -> str:
    """
    Clave de cruce con fuentes externas (población del INE, límites administrativos).

    Nombre sin artículos ni acentos, con la provincia como desambiguador: hay topónimos
    repetidos entre provincias gallegas.
    """
    palabras = [p for p in normalizar(municipio).split() if p not in ARTICULOS]
    return f"{' '.join(palabras)}|{normalizar(provincia)}"


def agregar(df: pd.DataFrame) -> pd.DataFrame:
    sin_municipio = int(df["municipio"].isna().sum())
    df = df[df["municipio"].notna()].copy()

    df["plazas"] = pd.to_numeric(df["plazas"], errors="coerce")

    agrupado = df.groupby(["provincia", "municipio"], dropna=False).agg(
        n_vut=("id_fuente", "count"),
        n_con_plazas=("plazas", "count"),
        plazas_total=("plazas", "sum"),
        plazas_media=("plazas", "mean"),
        plazas_mediana=("plazas", "median"),
    ).reset_index()

    agrupado["pct_con_plazas"] = (
        100 * agrupado["n_con_plazas"] / agrupado["n_vut"]
    ).round(1)
    agrupado["plazas_media"] = agrupado["plazas_media"].round(1)

    # `plazas_total` sólo suma las viviendas que declaran plazas. Cuando la cobertura es
    # parcial, el total infraestima; esta estimación completa las que faltan con la media
    # del propio concello y se publica aparte, nunca mezclada con el dato medido.
    agrupado["plazas_estimadas"] = (
        agrupado["plazas_total"]
        + (agrupado["n_vut"] - agrupado["n_con_plazas"]) * agrupado["plazas_media"].fillna(0)
    ).round(0)

    agrupado["ccaa"] = "Galicia"
    agrupado["clave_join"] = [
        clave_join(m, p) for m, p in zip(agrupado["municipio"], agrupado["provincia"])
    ]
    agrupado["resolucion_espacial"] = "municipal"
    agrupado["fuente"] = "REAT - Xunta de Galicia"
    agrupado["origen_agregacion"] = "municipal_directo"

    # Lo rellena resolver_codigo_ine(); se crea aquí para fijar el orden de columnas.
    agrupado["codigo_ine"] = pd.NA
    # A rellenar cuando se incorpore la población del INE.
    agrupado["poblacion"] = pd.NA
    agrupado["plazas_por_1000_hab"] = pd.NA

    columnas = [
        "ccaa", "provincia", "municipio", "clave_join", "codigo_ine",
        "n_vut", "n_con_plazas", "pct_con_plazas",
        "plazas_total", "plazas_estimadas", "plazas_media", "plazas_mediana",
        "poblacion", "plazas_por_1000_hab",
        "resolucion_espacial", "origen_agregacion", "fuente",
    ]
    agrupado = agrupado[columnas].sort_values("n_vut", ascending=False)
    agrupado.attrs["sin_municipio"] = sin_municipio
    return agrupado


def resolver_codigo_ine(agrupado: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Asigna el código INE a cada concello y añade los que no tienen ninguna VUT.

    Dos pasos: primero las equivalencias explícitas de denominación alternativa, y luego
    el cruce por nombre normalizado. Los concellos que el INE tiene y el registro no
    aparecen con `n_vut = 0` y `origen_agregacion = 'municipal_directo'`: sabemos que no
    tienen VUT registradas, así que es un cero real y no un hueco de cobertura.
    """
    municipios = pd.read_csv(ENTRADA_MUNICIPIOS, dtype={"codigo_ine": str})
    gal = municipios[municipios["ccaa"] == "Galicia"].copy()
    gal["k"] = gal["nombre_municipio"].map(
        lambda n: " ".join(p for p in normalizar(n).split() if p not in ARTICULOS)
    )
    por_nombre = dict(zip(gal["k"], gal["codigo_ine"]))

    codigos, via = [], []
    for municipio in agrupado["municipio"]:
        explicito = EQUIVALENCIAS_INE.get(str(municipio).strip().upper())
        if explicito:
            codigos.append(explicito)
            via.append("equivalencia")
            continue
        clave = " ".join(p for p in normalizar(municipio).split() if p not in ARTICULOS)
        codigos.append(por_nombre.get(clave))
        via.append("nombre" if clave in por_nombre else "sin_resolver")

    agrupado = agrupado.copy()
    agrupado["codigo_ine"] = codigos

    stats = {
        "con_vut": len(agrupado),
        "resueltos": int(pd.Series(codigos).notna().sum()),
        "por_equivalencia": via.count("equivalencia"),
        "sin_resolver": [
            m for m, v in zip(agrupado["municipio"], via) if v == "sin_resolver"
        ],
    }

    # Concellos del INE sin ninguna VUT registrada: cero real.
    presentes = set(c for c in codigos if c)
    faltan = gal[~gal["codigo_ine"].isin(presentes)]
    if len(faltan):
        ceros = pd.DataFrame({
            "ccaa": "Galicia",
            "provincia": faltan["provincia"].values,
            "municipio": faltan["nombre_municipio"].values,
            "clave_join": [clave_join(m, p) for m, p in
                           zip(faltan["nombre_municipio"], faltan["provincia"])],
            "codigo_ine": faltan["codigo_ine"].values,
            "n_vut": 0, "n_con_plazas": 0, "pct_con_plazas": 0.0,
            "plazas_total": 0.0, "plazas_estimadas": 0.0,
            # float("nan") y no pd.NA: en columnas numéricas todo-NA, pd.NA hace que
            # concat avise de un cambio futuro en la inferencia de tipos.
            "plazas_media": float("nan"), "plazas_mediana": float("nan"),
            "poblacion": float("nan"), "plazas_por_1000_hab": float("nan"),
            "resolucion_espacial": "municipal",
            "origen_agregacion": "municipal_directo",
            "fuente": "REAT - Xunta de Galicia",
        })
        agrupado = pd.concat([agrupado, ceros], ignore_index=True)

    stats["sin_vut"] = len(faltan)
    stats["total"] = len(agrupado)
    stats["municipios_ine_galicia"] = len(gal)
    return agrupado.sort_values("n_vut", ascending=False), stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agrega las VUT de Galicia por concello (resolución municipal)."
    )
    parser.parse_args()

    if not ENTRADA.exists():
        print(f"ERROR: falta {ENTRADA}", file=sys.stderr)
        print("Ejecuta antes: python etl/extract/extract_vut_oficial.py --fuentes galicia",
              file=sys.stderr)
        return 1

    df = pd.read_csv(ENTRADA, low_memory=False)
    print(f"Galicia: {len(df):,} registros de VUT")

    agrupado = agregar(df)
    sin_municipio = agrupado.attrs.get("sin_municipio", 0)

    stats = {}
    if ENTRADA_MUNICIPIOS.exists():
        agrupado, stats = resolver_codigo_ine(agrupado)
    else:
        print(f"  AVISO: falta {ENTRADA_MUNICIPIOS.name}; no se resuelve el código INE.",
              file=sys.stderr)
        print("  Ejecuta antes: python etl/extract/extract_ine_municipios.py", file=sys.stderr)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    agrupado.to_csv(SALIDA, index=False, encoding="utf-8")

    total_vut = int(agrupado["n_vut"].sum())
    total_plazas = int(agrupado["plazas_total"].sum())
    total_est = int(agrupado["plazas_estimadas"].sum())
    cobertura = 100 * agrupado["n_con_plazas"].sum() / total_vut

    print("\n" + "=" * 66)
    print("AGREGACIÓN MUNICIPAL — GALICIA")
    print("=" * 66)
    print(f"  Concellos:                 {len(agrupado):>8,}")
    print(f"  VUT agregadas:             {total_vut:>8,}")
    if sin_municipio:
        print(f"  Descartadas sin concello:  {sin_municipio:>8,}")
    print(f"  Plazas declaradas:         {total_plazas:>8,}  "
          f"(cobertura {cobertura:.1f} %)")
    print(f"  Plazas estimadas:          {total_est:>8,}  "
          f"(completando las no declaradas con la media del concello)")

    if stats:
        print("\n  Cruce con el código INE:")
        print(f"    Concellos con VUT:       {stats['con_vut']:>8,}")
        print(f"    Resueltos:               {stats['resueltos']:>8,}  "
              f"({stats['por_equivalencia']} por equivalencia de denominación)")
        if stats["sin_resolver"]:
            print(f"    SIN RESOLVER:            {len(stats['sin_resolver']):>8,}  "
                  f"{stats['sin_resolver'][:5]}")
        print(f"    Concellos sin VUT:       {stats['sin_vut']:>8,}  "
              f"(cargados como cero real)")
        print(f"    Total de filas:          {stats['total']:>8,}  "
              f"de {stats['municipios_ine_galicia']:,} concellos del INE")

    print("\n  Concellos con más VUT:")
    for _, f in agrupado.head(10).iterrows():
        print(f"    {f['municipio']:<26} {int(f['n_vut']):>6,} VUT  "
              f"{int(f['plazas_total']):>7,} plazas  ({f['pct_con_plazas']:>5.1f} % declaran)")

    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    print("\n  Siguiente paso: cruzar `clave_join` con la población municipal del INE")
    print("  para rellenar `poblacion` y `plazas_por_1000_hab`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
