"""
Extracción de la Encuesta de Ocupación Hotelera (EOH) del INE.

Cubre el hueco hotelero del indicador de saturación: hasta ahora sólo medía viviendas de
uso turístico, de modo que los destinos de turismo hotelero (Alcúdia, Benidorm, Salou)
aparecían como poco saturados cuando son de los que más presión soportan.

Misma arquitectura de capas desiguales que con las VUT: la EOH es la **capa base nacional**
—cobertura completa pero resolución provincial— y los registros hoteleros autonómicos serán
después la capa de precisión municipal.

Series utilizadas (API Tempus3 del INE, operación EOH / IOE 30235):

    Tabla 2066   Establecimientos, plazas, grados de ocupación y personal empleado
                 por comunidades autónomas y provincias.  Cobertura nacional.
    Tabla 2076   Establecimientos, plazas estimadas, grados de ocupación y personal
                 empleado por puntos turísticos.  138 municipios que el INE monitoriza
                 individualmente.

De las 7 medidas de cada tabla se toman `Número de plazas estimadas` y
`Número de establecimientos abiertos estimados`.

Estacionalidad: la EOH es mensual y se toma el **mes de máxima capacidad de los últimos 12**,
guardando también la media anual. Las plazas de VUT son capacidad registrada —siempre
"abierta" sobre el papel—, mientras que las plazas de la EOH son capacidad efectivamente
abierta ese mes. Promediar el año penalizaría a los destinos estacionales de costa, que son
precisamente los que el indicador de sólo-VUT ya infravaloraba; el pico es la comparación
homogénea.

Uso:
    python etl/extract/extract_eoh_ine.py
    python etl/extract/extract_eoh_ine.py --meses 24
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

BASE = "https://servicios.ine.es/wstempus/js/ES"
UA = {"User-Agent": "TFM-TUI-Dashboard/0.1 (proyecto academico TFM)"}
TIMEOUT = 600

TABLA_PROVINCIAS = 2066
TABLA_PUNTOS = 2076

MEDIDA_PLAZAS = "número de plazas estimadas"
MEDIDA_ESTABLECIMIENTOS = "número de establecimientos abiertos estimados"

SALIDA = PROCESSED_DIR / "eoh_hotelera.csv"

ARTICULOS = {"a", "o", "as", "os", "el", "la", "los", "las", "lo", "l", "es", "sa", "sant", "santa"}


def normalizar(texto: object) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def claves_nombre(texto: object) -> list[str]:
    """
    Variantes de un topónimo, para cruzarlo entre fuentes que lo escriben distinto.

    El INE alterna "Alacant/Alicante" y "Alicante/Alacant", y antepone o pospone el
    artículo. Se generan las formas separadas por '/' y ',', sin artículos, y una variante
    con las palabras ordenadas para que el orden deje de importar.
    """
    bruto = str(texto) if texto is not None else ""
    partes = re.split(r"[/,]", bruto) + [bruto]
    claves = set()
    for p in partes:
        palabras = [w for w in normalizar(p).split() if w not in ARTICULOS]
        if palabras:
            claves.add(" ".join(palabras))
            claves.add(" ".join(sorted(palabras)))
    return sorted(claves, key=len, reverse=True)


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def descargar_tabla(tabla: int, meses: int) -> list[dict]:
    url = f"{BASE}/DATOS_TABLA/{tabla}?nult={meses}"
    print(f"  Tabla {tabla}: descargando {meses} meses…")
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    datos = r.json()
    print(f"  Tabla {tabla}: {len(datos):,} series".replace(",", "."))
    return datos


def extraer_medida(series: list[dict], medida: str) -> pd.DataFrame:
    """
    Reduce las series a una fila por ámbito con el pico y la media de los meses disponibles.

    El nombre de la serie del INE concatena los valores de cada dimensión separados por
    puntos: "Nacional. Número de plazas estimadas. Benidorm.". El ámbito es lo que queda al
    quitar el prefijo nacional y la medida.
    """
    # La medida llega con tildes y `normalizar` las quita: hay que normalizar también el
    # patrón, o la comparación no casa nunca.
    objetivo = normalizar(medida)

    # Partes que no identifican al territorio: son otras dimensiones de la tabla que el
    # INE concatena en el mismo nombre de serie.
    RELLENO = {"nacional", "total categorias", "dato", "establecimientos hoteleros",
               "total", "total establecimientos"}

    filas = []
    for s in series:
        nombre = s.get("Nombre") or ""
        partes = [p.strip() for p in nombre.split(".") if p.strip()]
        if not any(normalizar(p) == objetivo for p in partes):
            continue

        candidatas = [
            p for p in partes
            if normalizar(p) != objetivo and normalizar(p) not in RELLENO
        ]
        # Algunas series traen el ámbito con varias dimensiones separadas por '/'.
        trozos = [t.strip() for c in candidatas for t in c.split("/") if t.strip()]
        trozos = [t for t in trozos if normalizar(t) not in RELLENO]
        ambito = trozos[-1] if trozos else "Total Nacional"

        # Regalo del INE: parte de las series nombran el ámbito como "28079-Madrid", con
        # el código municipal incluido. Cuando está, se usa y se evita todo el riesgo de
        # cruzar por nombre.
        codigo_directo = None
        m = re.match(r"^(\d{5})\s*-\s*(.+)$", ambito)
        if m:
            codigo_directo, ambito = m.group(1), m.group(2).strip()

        valores = [(d.get("Anyo"), d.get("FK_Periodo"), d.get("Valor"))
                   for d in s.get("Data", []) if d.get("Valor") is not None]
        if not valores:
            continue

        serie = pd.Series([v for _, _, v in valores])
        pico_idx = int(serie.idxmax())
        filas.append({
            "ambito_nombre": ambito,
            "codigo_directo": codigo_directo,
            "valor_pico": float(serie.max()),
            "valor_medio": round(float(serie.mean()), 1),
            "periodo_pico": f"{valores[pico_idx][0]}-M{valores[pico_idx][1]:02d}",
            "meses_disponibles": len(valores),
        })
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Mapeo de puntos turísticos a código INE
# ---------------------------------------------------------------------------

# Puntos turísticos cuyo nombre en la EOH no casa con el del INE.
#
# Se mapea **nombre a nombre**, nunca a un código escrito a mano. Los códigos se resuelven
# en tiempo de ejecución contra municipios_ine.csv, y si una equivalencia no resuelve se
# avisa en pantalla. Una versión anterior fijaba los códigos directamente y 12 de 106
# estaban mal: 'capdepera' apuntaba a Campos, 'salou' a Sant Jaume dels Domenys y 'teguise'
# a Tejeda. El efecto no era un fallo visible sino plazas hoteleras atribuidas al municipio
# equivocado, que en el indicador aparecían como saturación disparada en pueblos pequeños.
#
# El valor es (nombre en el INE, provincia) — la provincia sólo hace falta para desambiguar.
EQUIVALENCIAS_PUNTOS_NOMBRE: dict[str, tuple[str, str | None]] = {
    "calpe": ("Calp", "Alicante"),
    "palma de mallorca": ("Palma", "Balears, Illes"),
    "granada": ("Granada", "Granada"),
    "la oliva": ("Oliva, La", "Palmas, Las"),
    "oliva la": ("Oliva, La", "Palmas, Las"),
    "puerto de la cruz": ("Puerto de la Cruz", "Santa Cruz de Tenerife"),
    "santa cruz de tenerife": ("Santa Cruz de Tenerife", "Santa Cruz de Tenerife"),
    "las palmas de gran canaria": ("Palmas de Gran Canaria, Las", "Palmas, Las"),
    "donostia san sebastian": ("Donostia/San Sebastián", "Gipuzkoa"),
    "san sebastian": ("Donostia/San Sebastián", "Gipuzkoa"),
    "a coruna": ("Coruña, A", "Coruña, A"),
    "santiago de compostela": ("Santiago de Compostela", "Coruña, A"),
    "castello de la plana": ("Castelló de la Plana/Castellón de la Plana", "Castellón/Castelló"),
    # Nombres que existen en varias provincias: sin el desambiguador se quedan sin resolver.
    "alcudia": ("Alcúdia", "Balears, Illes"),
    "antigua": ("Antigua", "Palmas, Las"),
    "vitoria gasteiz": ("Vitoria-Gasteiz", "Araba/Álava"),
    "eivissa": ("Eivissa", "Balears, Illes"),
    "mao": ("Maó", "Balears, Illes"),
    "javea": ("Xàbia/Jávea", "Alicante"),
    "denia": ("Dénia", "Alicante"),
    "villajoyosa": ("Villajoyosa/Vila Joiosa, la", "Alicante"),
    "peniscola": ("Peníscola/Peñíscola", "Castellón/Castelló"),
    "oropesa del mar": ("Orpesa/Oropesa del Mar", "Castellón/Castelló"),
    "benicasim": ("Benicasim/Benicàssim", "Castellón/Castelló"),
    "salobrena": ("Salobreña", "Granada"),
    "almunecar": ("Almuñécar", "Granada"),
    "mazarron": ("Mazarrón", "Murcia"),
    "aguilas": ("Águilas", "Murcia"),
    "los alcazares": ("Alcázares, Los", "Murcia"),
    "pajara": ("Pájara", "Palmas, Las"),
    "mogan": ("Mogán", "Palmas, Las"),
    "tias": ("Tías", "Palmas, Las"),
    "calvia": ("Calvià", "Balears, Illes"),
    "pollenca": ("Pollença", "Balears, Illes"),
    "santanyi": ("Santanyí", "Balears, Illes"),
    "santa eulalia del rio": ("Santa Eulària des Riu", "Balears, Illes"),
    "sant josep de sa talaia": ("Sant Josep de sa Talaia", "Balears, Illes"),
    "sant antoni de portmany": ("Sant Antoni de Portmany", "Balears, Illes"),
    "puerto de santa maria": ("Puerto de Santa María, El", "Cádiz"),
    "sanlucar de barrameda": ("Sanlúcar de Barrameda", "Cádiz"),
    "castello d empuries": ("Castelló d'Empúries", "Girona"),
    "torroella de montgri": ("Torroella de Montgrí", "Girona"),
    "sant feliu de guixols": ("Sant Feliu de Guíxols", "Girona"),
}

# Diccionario histórico por código: se conserva vacío a propósito. Fijar códigos a mano
# resultó ser la vía por la que entraron los 12 errores descritos arriba.
EQUIVALENCIAS_PUNTOS: dict[str, str] = {}

_EQUIVALENCIAS_OBSOLETAS = {
    "donostia san sebastian": "20069",
    "san sebastian": "20069",
    "palma de mallorca": "07040",
    "las palmas de gran canaria": "35016",
    "santa cruz de tenerife": "38038",
    "san bartolome de tirajana": "35020",
    "santiago de compostela": "15078",
    "a coruna": "15030",
    "ourense": "32054",
    "girona": "17079",
    "lleida": "25120",
    "castello de la plana": "12040",
    "alacant alicante": "03014",
    "vitoria gasteiz": "01059",
    "donostia": "20069",
    "eivissa": "07026",
    "mao": "07032",
    "ciutadella de menorca": "07015",
    "arona": "38006",
    "adeje": "38001",
    "calvia": "07011",
    "benidorm": "03031",
    "salou": "43137",
    "lloret de mar": "17095",
    "marbella": "29069",
    "torremolinos": "29901",
    "benalmadena": "29025",
    "fuengirola": "29054",
    "roquetas de mar": "04079",
    "conil de la frontera": "11014",
    "chiclana de la frontera": "11015",
    "gandia": "46131",
    "peniscola": "12089",
    "oropesa del mar": "12085",
    "cambrils": "43038",
    "sitges": "08270",
    "castelldefels": "08056",
    "santa susanna": "08260",
    "pineda de mar": "08162",
    "malgrat de mar": "08110",
    "tossa de mar": "17218",
    "roses": "17152",
    "castello d empuries": "17047",
    "torroella de montgri": "17199",
    "sant feliu de guixols": "17160",
    "blanes": "17023",
    "calonge": "17034",
    "palafrugell": "17117",
    "puerto de la cruz": "38031",
    "santiago del teide": "38041",
    "guia de isora": "38019",
    "granadilla de abona": "38017",
    "pajara": "35015",
    "la oliva": "35014",
    "antigua": "35003",
    "teguise": "35025",
    "tias": "35026",
    "yaiza": "35034",
    "mogan": "35012",
    "alcudia": "07003",
    "muro": "07039",
    "sant llorenc des cardassar": "07045",
    "capdepera": "07013",
    "son servera": "07061",
    "manacor": "07033",
    "llucmajor": "07031",
    "santanyi": "07057",
    "pollenca": "07042",
    "sant antoni de portmany": "07046",
    "santa eulalia del rio": "07054",
    "sant josep de sa talaia": "07048",
    "torrevieja": "03133",
    "santa pola": "03121",
    "javea": "03082",
    "denia": "03063",
    "calpe": "03047",
    "altea": "03018",
    "villajoyosa": "03139",
    "orihuela": "03099",
    "cullera": "46105",
    "benicasim": "12028",
    "vinaros": "12138",
    "mazarron": "30026",
    "aguilas": "30003",
    "cartagena": "30016",
    "san javier": "30035",
    "los alcazares": "30902",
    "nerja": "29075",
    "torrox": "29092",
    "estepona": "29051",
    "mijas": "29070",
    "almunecar": "18017",
    "motril": "18140",
    "salobrena": "18175",
    "ayamonte": "21010",
    "isla cristina": "21042",
    "punta umbria": "21060",
    "cartaya": "21021",
    "tarifa": "11035",
    "barbate": "11007",
    "vejer de la frontera": "11039",
    "rota": "11030",
    "sanlucar de barrameda": "11032",
    "puerto de santa maria": "11027",
    "benicarlo": "12027",
    "granada": "18087",
    "la oliva": "35014",
    "puerto de la cruz": "38031",
}


def clave_estricta(texto: object) -> str:
    """
    Clave que **conserva los artículos** y ordena las palabras.

    Hace equivalentes "Oliva, La" y "La Oliva" sin confundirlos con "Oliva" (Valencia),
    que es un municipio distinto. Quitar el artículo, como hace `claves_nombre`, fusiona
    los dos y deja el nombre ambiguo.
    """
    return " ".join(sorted(normalizar(texto).split()))


# Índice de equivalencias por clave estricta, para que el diccionario se consulte con la
# misma normalización con la que se generan las claves.
EQUIVALENCIAS_ESTRICTAS = {
    clave_estricta(k): v for k, v in EQUIVALENCIAS_PUNTOS_NOMBRE.items()
}


def resolver_equivalencias(municipios: pd.DataFrame) -> tuple[dict[str, str], list[str]]:
    """
    Convierte las equivalencias nombre→nombre en nombre→código, contra el fichero del INE.

    Devuelve también las que no resuelven, para que se vean en pantalla en vez de
    desaparecer en silencio.
    """
    por_nombre_prov: dict[tuple[str, str], str] = {}
    por_nombre: dict[str, list[str]] = {}
    for r in municipios.itertuples(index=False):
        cn = clave_estricta(r.nombre_municipio)
        por_nombre_prov[(cn, clave_estricta(r.provincia))] = r.codigo_ine
        por_nombre.setdefault(cn, []).append(r.codigo_ine)

    resueltas, fallidas = {}, []
    for clave, (nombre_ine, provincia) in EQUIVALENCIAS_ESTRICTAS.items():
        cn = clave_estricta(nombre_ine)
        codigo = None
        if provincia:
            codigo = por_nombre_prov.get((cn, clave_estricta(provincia)))
        if codigo is None:
            candidatos = por_nombre.get(cn, [])
            codigo = candidatos[0] if len(candidatos) == 1 else None
        if codigo:
            resueltas[clave] = codigo
        else:
            fallidas.append(f"{clave} -> {nombre_ine} ({provincia})")
    return resueltas, fallidas


def mapear_puntos(df: pd.DataFrame, municipios: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Asigna el código INE municipal a cada punto turístico de la EOH."""
    equivalencias, fallidas = resolver_equivalencias(municipios)
    if fallidas:
        print(f"  AVISO: {len(fallidas)} equivalencias no resuelven contra el INE:")
        for f in fallidas:
            print(f"    - {f}")

    idx: dict[str, list[str]] = {}
    for r in municipios.itertuples(index=False):
        for clave in claves_nombre(r.nombre_municipio):
            idx.setdefault(clave, []).append(r.codigo_ine)

    codigos, via = [], []
    directos = df["codigo_directo"] if "codigo_directo" in df.columns else [None] * len(df)
    for nombre, directo in zip(df["nombre"], directos):
        if directo:
            codigos.append(str(directo).zfill(5))
            via.append("codigo_ine_en_serie")
            continue
        codigo = None
        # Primero la clave estricta (conserva artículos): distingue "La Oliva" de "Oliva".
        estricta = clave_estricta(nombre)
        if estricta in equivalencias:
            codigo = equivalencias[estricta]
            via.append("equivalencia")
        if codigo is None:
            for clave in claves_nombre(nombre):
                candidatos = idx.get(clave, [])
                if len(candidatos) == 1:
                    codigo = candidatos[0]
                    via.append("nombre")
                    break
        if codigo is None:
            via.append("sin_resolver")
        codigos.append(codigo)

    df = df.copy()
    df["codigo_ine"] = codigos
    stats = {
        "puntos": len(df),
        "resueltos": int(pd.Series(codigos).notna().sum()),
        "por_codigo": via.count("codigo_ine_en_serie"),
        "por_equivalencia": via.count("equivalencia"),
        "por_nombre": via.count("nombre"),
        "sin_resolver": [n for n, v in zip(df["nombre"], via) if v == "sin_resolver"],
    }
    return df, stats


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Descarga la EOH del INE.")
    parser.add_argument("--meses", type=int, default=12,
                        help="Meses hacia atrás a considerar (12 por defecto).")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    inicio = time.time()

    print("[1/4] Descarga de la EOH")
    try:
        crudo_prov = descargar_tabla(TABLA_PROVINCIAS, args.meses)
        crudo_pt = descargar_tabla(TABLA_PUNTOS, args.meses)
    except requests.RequestException as exc:
        print(f"\nERROR de descarga: {exc}", file=sys.stderr)
        return 2

    (RAW_DIR / f"eoh_provincias_{TABLA_PROVINCIAS}.json").write_text(
        json.dumps(crudo_prov, ensure_ascii=False), encoding="utf-8")
    (RAW_DIR / f"eoh_puntos_turisticos_{TABLA_PUNTOS}.json").write_text(
        json.dumps(crudo_pt, ensure_ascii=False), encoding="utf-8")

    print("\n[2/4] Extracción de medidas")
    filas = []
    for etiqueta, crudo, ambito in (
        ("provincias", crudo_prov, "provincia"),
        ("puntos turísticos", crudo_pt, "punto_turistico"),
    ):
        plazas = extraer_medida(crudo, MEDIDA_PLAZAS)
        establec = extraer_medida(crudo, MEDIDA_ESTABLECIMIENTOS)
        unido = plazas.merge(
            establec[["ambito_nombre", "valor_pico", "valor_medio"]],
            on="ambito_nombre", how="outer", suffixes=("_plazas", "_establec"),
        )
        unido["ambito"] = ambito
        filas.append(unido)
        print(f"  {etiqueta:<18} {len(unido):>4} ámbitos")

    eoh = pd.concat(filas, ignore_index=True)
    eoh = eoh.rename(columns={
        "valor_pico_plazas": "plazas_hoteleras",
        "valor_medio_plazas": "plazas_hoteleras_media_anual",
        "valor_pico_establec": "n_establecimientos",
        "valor_medio_establec": "n_establecimientos_media_anual",
        "ambito_nombre": "nombre",
    })

    print("\n[3/4] Mapeo de puntos turísticos a código INE")
    ruta_muni = PROCESSED_DIR / "municipios_ine.csv"
    stats = {}
    if ruta_muni.exists():
        municipios = pd.read_csv(ruta_muni, dtype={"codigo_ine": str})
        es_pt = eoh["ambito"] == "punto_turistico"
        mapeados, stats = mapear_puntos(eoh[es_pt], municipios)
        eoh.loc[es_pt, "codigo_ine"] = mapeados["codigo_ine"].values
        print(f"  Puntos turísticos:  {stats['puntos']:>4}")
        print(f"  Resueltos:          {stats['resueltos']:>4}  "
              f"({stats['por_codigo']} por código en la serie, "
              f"{stats['por_nombre']} por nombre, {stats['por_equivalencia']} por equivalencia)")
        if stats["sin_resolver"]:
            print(f"  SIN RESOLVER ({len(stats['sin_resolver'])}):")
            for n in stats["sin_resolver"]:
                print(f"    - {n}")
    else:
        print(f"  AVISO: falta {ruta_muni.name}; no se mapean los puntos turísticos.")
        eoh["codigo_ine"] = pd.NA

    print("\n[4/4] Salida")
    eoh["periodo"] = eoh["periodo_pico"]
    eoh["fuente"] = "INE - Encuesta de Ocupación Hotelera (tablas 2066 y 2076)"
    eoh["codigo"] = eoh["codigo_ine"]

    columnas = [
        "ambito", "codigo", "nombre", "plazas_hoteleras", "n_establecimientos",
        "plazas_hoteleras_media_anual", "n_establecimientos_media_anual",
        "periodo", "meses_disponibles", "fuente",
    ]
    eoh = eoh[columnas].sort_values(["ambito", "nombre"])
    eoh.to_csv(SALIDA, index=False, encoding="utf-8")

    # ---------------- Resumen ----------------
    print("\n" + "=" * 72)
    print("ENCUESTA DE OCUPACIÓN HOTELERA")
    print("=" * 72)
    for ambito, grupo in eoh.groupby("ambito"):
        con_codigo = int(grupo["codigo"].notna().sum())
        print(f"\n  {ambito}:  {len(grupo)} ámbitos"
              + (f", {con_codigo} con código INE" if ambito == "punto_turistico" else ""))
        print(f"    Plazas (pico):  {grupo['plazas_hoteleras'].sum():>12,.0f}".replace(",", "."))
        print(f"    Periodo pico más frecuente: {grupo['periodo'].mode().iloc[0]}")

    print("\n  Mayores puntos turísticos por plazas hoteleras:")
    pt = eoh[eoh["ambito"] == "punto_turistico"].nlargest(10, "plazas_hoteleras")
    for _, f in pt.iterrows():
        print(f"    {str(f['nombre'])[:30]:<30} {f['plazas_hoteleras']:>9,.0f} plazas  "
              .replace(",", ".") + f"{f['n_establecimientos']:>6,.0f} establec.".replace(",", "."))

    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    print(f"  Duración: {time.time() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
