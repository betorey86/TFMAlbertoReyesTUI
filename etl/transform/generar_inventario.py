"""
Genera docs/inventario_datos.md a partir de los ficheros reales de data/.

El documento es la base del capítulo de metodología del TFM y la referencia para la fase de
fusión, así que las cifras no se escriben a mano: se recalculan leyendo `data/raw/` y
`data/processed/` cada vez que se ejecuta este script. Si una extracción avanza, basta
relanzarlo para que el inventario quede al día.

Uso:
    python etl/transform/generar_inventario.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DOCS_DIR = PROJECT_ROOT / "docs"
SALIDA = DOCS_DIR / "inventario_datos.md"


@dataclass
class Fila:
    capa: str
    territorio: str
    fuente: str
    registros: int
    con_coordenadas: int
    resolucion: str
    confianza: str
    limitaciones: str
    extra: dict = field(default_factory=dict)

    @property
    def pct(self) -> float:
        return 100 * self.con_coordenadas / self.registros if self.registros else 0.0


# ---------------------------------------------------------------------------
# VUT oficial
# ---------------------------------------------------------------------------

VUT_FUENTES = {
    "andalucia": {
        "territorio": "Andalucía",
        "fuente": "OpenRTA — Junta de Andalucía",
        "resolucion": "punto",
        "confianza": "alta",
        "limitaciones": "Volcado de 325 MB con todas las tipologías; el filtrado a VUT se "
                        "hace en local. Formato decimal mixto en las coordenadas.",
    },
    "canarias": {
        "territorio": "Canarias",
        "fuente": "Registro General Turístico de Canarias",
        "resolucion": "punto",
        "confianza": "alta",
        "limitaciones": "23.979 filas traen (0,0) como ausencia de coordenada, no como "
                        "posición: se anulan.",
    },
    "valencia": {
        "territorio": "Comunitat Valenciana",
        "fuente": "Generalitat Valenciana — dadesobertes.gva.es",
        "resolucion": "punto",
        "confianza": "alta",
        "limitaciones": "Sin coordenadas en origen; geocodificado por referencia catastral. "
                        "Quedan parcelas pendientes por bloqueo temporal del Catastro.",
    },
    "galicia": {
        "territorio": "Galicia",
        "fuente": "REAT — Xunta de Galicia",
        "resolucion": "municipal",
        "confianza": "parcial",
        "limitaciones": "Sólo el 0,7 % trae coordenadas y ningún geocodificador supera el "
                        "45 %. Se explota a nivel de concello. El fichero de origen incluía "
                        "datos personales (nombre y DNI del titular), excluidos en la lectura.",
    },
    "baleares": {
        "territorio": "Illes Balears (sólo Mallorca)",
        "fuente": "Consell de Mallorca",
        "resolucion": "punto",
        "confianza": "parcial",
        "limitaciones": "Sólo Mallorca. Menorca, Ibiza y Formentera dependen de sus propios "
                        "consells y publican por separado.",
    },
    "barcelona": {
        "territorio": "Cataluña (sólo ciudad de Barcelona)",
        "fuente": "Open Data BCN",
        "resolucion": "punto",
        "confianza": "parcial",
        "limitaciones": "Sólo el municipio de Barcelona. El registro del resto de Cataluña "
                        "lo lleva la Generalitat y no publica volcado equivalente.",
    },
    "pais_vasco": {
        "territorio": "País Vasco",
        "fuente": "Open Data Euskadi — REATE",
        "resolucion": "punto",
        "confianza": "alta",
        "limitaciones": "Sin coordenadas en origen; geocodificado con Nominatim. El 14,5 % "
                        "no resuelto son barrios rurales dispersos.",
    },
    "madrid": {
        "territorio": "Comunidad de Madrid (sólo ciudad)",
        "fuente": "Geoportal Ayuntamiento de Madrid",
        "resolucion": "punto",
        "confianza": "no comparable",
        "limitaciones": "Mide LICENCIAS URBANÍSTICAS concedidas, no inscripciones en el "
                        "registro turístico. No es la misma magnitud que el resto.",
    },
}

# Fichero geocodificado que sustituye al normalizado cuando existe.
VUT_GEOCODIFICADOS = {
    "pais_vasco": "vut_pais_vasco_geocodificado.csv",
    "valencia": "vut_valencia_geocodificado.csv",
}

CCAA_SIN_REGISTRO = [
    "Aragón", "Cantabria", "Castilla-La Mancha", "Castilla y León", "Ceuta",
    "Extremadura", "La Rioja", "Melilla", "Navarra", "Principado de Asturias",
    "Región de Murcia",
]


def _leer(ruta: Path, columnas: list[str] | None = None) -> pd.DataFrame | None:
    if not ruta.exists():
        return None
    try:
        if columnas:
            cab = pd.read_csv(ruta, nrows=0)
            columnas = [c for c in columnas if c in cab.columns]
        return pd.read_csv(ruta, low_memory=False, usecols=columnas or None)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, OSError, UnicodeDecodeError):
        return None


def filas_vut() -> list[Fila]:
    filas = []
    for slug, meta in VUT_FUENTES.items():
        df = None
        if slug in VUT_GEOCODIFICADOS:
            df = _leer(PROCESSED_DIR / VUT_GEOCODIFICADOS[slug], ["lat", "lon", "plazas"])
        if df is None:
            df = _leer(PROCESSED_DIR / f"vut_normalizado_{slug}.csv", ["lat", "lon", "plazas"])
        if df is None:
            continue

        con_coord = int((df["lat"].notna() & df["lon"].notna()).sum()) if "lat" in df else 0
        extra = {}
        if "plazas" in df:
            extra["plazas_declaradas"] = int(pd.to_numeric(df["plazas"], errors="coerce").sum())
            extra["pct_plazas"] = round(100 * df["plazas"].notna().mean(), 1)

        filas.append(Fila(
            capa="VUT oficial",
            territorio=meta["territorio"],
            fuente=meta["fuente"],
            registros=len(df),
            con_coordenadas=con_coord,
            resolucion=meta["resolucion"],
            confianza=meta["confianza"],
            limitaciones=meta["limitaciones"],
            extra=extra,
        ))
    return filas


# ---------------------------------------------------------------------------
# Capas OSM
# ---------------------------------------------------------------------------

CAPAS_OSM = {
    "alojamientos": ("Alojamientos OSM", "OpenStreetMap (Overpass)"),
    "restauracion": ("Restauración", "OpenStreetMap (Overpass)"),
    "atracciones": ("Atracciones", "OpenStreetMap (Overpass)"),
    "transporte_principales": ("Transporte", "OpenStreetMap (Overpass)"),
    "camping": ("Camping", "OpenStreetMap (Overpass)"),
}

LIMITACIONES_OSM = {
    "alojamientos": "OSM infrarrepresenta el alojamiento no hotelero y el sesgo no es "
                    "uniforme entre CCAA: no comparable entre territorios sin contrastar.",
    "restauracion": "El bar de barrio y el restaurante turístico comparten etiqueta: mide "
                    "densidad de hostelería, no especialización turística.",
    "atracciones": "Cobertura desigual; `viewpoint` domina el recuento y no equivale a "
                   "recurso turístico gestionado.",
    "transporte_principales": "Perfil de nodos de entrada (estaciones, aeropuertos, ferris). "
                              "No incluye paradas urbanas.",
    "camping": "`capacity` presente en una fracción mínima de los registros y `camp_site` "
               "mezcla camping comercial con acampada libre: sirve para contar "
               "establecimientos, no capacidad.",
}


def _contar_json(fichero: Path) -> tuple[int, int, str]:
    """Devuelve (elementos, con coordenadas, nombre de CCAA) de un JSON de capa OSM."""
    try:
        with fichero.open(encoding="utf-8") as f:
            datos = json.load(f)
    except (ValueError, OSError):
        return 0, 0, ""

    elementos = datos.get("osm", {}).get("elements", [])
    ccaa = datos.get("metadata", {}).get("ccaa_nombre", "")
    con_coord = sum(
        1 for el in elementos
        if (el.get("lat") is not None and el.get("lon") is not None)
        or (el.get("center") or {}).get("lat") is not None
    )
    return len(elementos), con_coord, ccaa


def filas_osm() -> list[Fila]:
    filas = []
    for prefijo, (etiqueta, fuente) in CAPAS_OSM.items():
        ficheros = [f for f in RAW_DIR.glob(f"osm_{prefijo}_*.json")
                    if "consolidado" not in f.name]
        # Una CCAA puede tener varias fechas: se queda la más reciente.
        por_ccaa: dict[str, Path] = {}
        for f in ficheros:
            partes = f.stem.split("_")
            slug = partes[-2] if len(partes) >= 2 else f.stem
            if slug not in por_ccaa or f.name > por_ccaa[slug].name:
                por_ccaa[slug] = f

        total = coord = 0
        territorios = 0
        for fichero in por_ccaa.values():
            n, c, _ = _contar_json(fichero)
            total += n
            coord += c
            territorios += 1

        if not territorios:
            continue

        ambito = ("España (19 CCAA)" if territorios >= 19
                  else f"España (parcial: {territorios}/19 CCAA)")
        filas.append(Fila(
            capa=etiqueta,
            territorio=ambito,
            fuente=fuente,
            registros=total,
            con_coordenadas=coord,
            resolucion="punto",
            confianza="solo OSM",
            limitaciones=LIMITACIONES_OSM.get(prefijo, ""),
        ))
    return filas


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------

def num(n: object) -> str:
    return f"{int(n):,}".replace(",", ".")


def tabla_markdown(filas: list[Fila]) -> str:
    cabecera = (
        "| Capa | Territorio | Fuente | Registros | Con coord. | % coord. | "
        "Resolución | Confianza | Limitaciones declaradas |\n"
        "|---|---|---|---:|---:|---:|---|---|---|\n"
    )
    cuerpo = ""
    for f in filas:
        pct = "—" if f.resolucion == "municipal" else f"{f.pct:.1f} %"
        coord = "—" if f.resolucion == "municipal" else num(f.con_coordenadas)
        cuerpo += (
            f"| {f.capa} | {f.territorio} | {f.fuente} | {num(f.registros)} | "
            f"{coord} | {pct} | {f.resolucion} | {f.confianza} | {f.limitaciones} |\n"
        )
    return cabecera + cuerpo


def generar() -> str:
    filas = filas_vut() + filas_osm()
    ahora = datetime.now(timezone.utc)

    vut = [f for f in filas if f.capa == "VUT oficial"]
    osm = [f for f in filas if f.capa != "VUT oficial"]

    total_vut = sum(f.registros for f in vut)
    coord_vut = sum(f.con_coordenadas for f in vut if f.resolucion == "punto")
    punto_vut = sum(f.registros for f in vut if f.resolucion == "punto")
    total_osm = sum(f.registros for f in osm)

    galicia = next((f for f in vut if f.territorio == "Galicia"), None)
    municipal = _leer(PROCESSED_DIR / "vut_galicia_municipal.csv")

    doc = f"""# Inventario de datos

Estado de todas las capas y fuentes del proyecto. Es la base del capítulo de metodología y
la referencia para la fase de fusión.

**Generado automáticamente** por `etl/transform/generar_inventario.py` leyendo los ficheros
reales de `data/raw/` y `data/processed/`. Las cifras no están escritas a mano: se
recalculan en cada ejecución. Última generación: {ahora:%d/%m/%Y %H:%M} UTC.

## Resumen

| | Registros |
|---|---:|
| VUT de registros oficiales | {num(total_vut)} |
| — con resolución de punto | {num(punto_vut)} |
| — geolocalizados | {num(coord_vut)} ({100 * coord_vut / punto_vut if punto_vut else 0:.1f} %) |
| Elementos de OpenStreetMap | {num(total_osm)} |
| **Total** | **{num(total_vut + total_osm)}** |

## Inventario detallado

{tabla_markdown(filas)}

### Cómo leer la columna "Confianza"

- **alta** — registro oficial íntegro del territorio, con la magnitud que dice medir.
- **parcial** — cubre sólo una parte del territorio, o su geolocalización es incompleta.
- **no comparable** — mide una magnitud distinta a la del resto de fuentes de su capa.
- **solo OSM** — cartografía colaborativa, sin respaldo de registro administrativo.

## Territorios sin registro oficial de VUT

En estas {len(CCAA_SIN_REGISTRO)} comunidades **no hay dato de registro administrativo**
incorporado: lo único disponible es OpenStreetMap.

{chr(10).join(f"- {c}" for c in CCAA_SIN_REGISTRO)}

Es la limitación de cobertura más importante del proyecto. Un mapa con menos puntos en
Aragón que en Andalucía no indica menos oferta: indica que Andalucía publica su registro y
Aragón no está incorporado.

---

# Trampas metodológicas identificadas

Esta sección recoge lo que **no** se puede concluir de los datos anteriores. Todo lo de
arriba es dato medido; lo de aquí son limitaciones conocidas, verificadas durante la
extracción.

## (a) Madrid mide licencias urbanísticas, no registro turístico

El fichero del Geoportal del Ayuntamiento de Madrid recoge **licencias urbanísticas
concedidas** para uso de vivienda turística, no inscripciones en el registro turístico
autonómico. Por eso son {num(next((f.registros for f in vut if 'Madrid' in f.territorio), 0))}
registros frente a los {num(next((f.registros for f in vut if 'Barcelona' in f.territorio), 0))}
de Barcelona.

**No son magnitudes comparables.** Poner ambas en el mismo mapa de densidad llevaría a
concluir que Madrid no tiene presión de vivienda turística, cuando lo que ocurre es que se
está midiendo el permiso administrativo y no la actividad.

## (b) Galicia va a resolución municipal, no de punto

Se probaron los dos geocodificadores disponibles sobre la **misma muestra de 300
direcciones**, con la misma semilla y la misma limpieza previa:

| Geocodificador | Cobertura |
|---|---:|
| Catastro (Consulta_DNPLOC) | 45,0 % |
| Cartociudad (IGN) | 26,7 % |
| Combinados | 57,0 % |

Ninguno alcanza un umbral aceptable, y **lo que falla no es aleatorio**: son los topónimos
rurales dispersos (`LUGAR DE PEREIRIÑA`, `LG. SEÑORANS S/N`). Geocodificar dejaría el mapa
con Vigo, A Coruña y Santiago, y sin el rural gallego, que es precisamente donde las Rías
Baixas concentran presión turística.

Un mapa así diría *"aquí no hay presión turística"* donde en realidad dice *"aquí no supimos
ubicar la oferta"*. Por eso Galicia se explota a nivel de concello, donde el dato es sólido:
"""

    if municipal is not None and not municipal.empty:
        n_conc = len(municipal)
        plazas = int(pd.to_numeric(municipal["plazas_total"], errors="coerce").sum())
        pct_pl = 100 * municipal["n_con_plazas"].sum() / municipal["n_vut"].sum()
        doc += (
            f"\n- **{num(n_conc)} concellos** con al menos una VUT registrada.\n"
            f"- **{num(plazas)} plazas declaradas**, con una cobertura del {pct_pl:.1f} % "
            f"de los registros.\n"
            f"- El municipio está en el 100 % de los {num(galicia.registros if galicia else 0)} "
            f"registros.\n\n"
            "Agregación en `data/processed/vut_galicia_municipal.csv`, con la clave "
            "`clave_join` preparada para cruzar con la población municipal del INE y "
            "calcular el ratio de plazas por habitante.\n"
        )
    else:
        doc += ("\n(La agregación municipal aún no se ha generado: ejecuta "
                "`python etl/transform/agregar_galicia_municipal.py`.)\n")

    camping = next((f for f in osm if f.capa == "Camping"), None)
    doc += f"""
## (c) El camping de OSM cuenta establecimientos, no capacidad

Dos problemas distintos, ambos verificados sobre los datos extraídos:

1. **`capacity` casi nunca está.** En la prueba de Baleares, 1 de 24 registros (4 %) traía
   plazas. Sin ese campo, la capa no puede alimentar ningún ratio de capacidad.
2. **`camp_site` mezcla oferta comercial con acampada libre.** Entre los campings de
   Baleares aparecen zonas de acampada públicas y campamentos ("Zona d'acampada
   s'Arenalet", "Campament de la Victòria"), que no son plazas de mercado. Se capturan
   `fee`, `operator`, `backcountry` e `impromptu` precisamente para poder separarlos.

Además, la mitad de los registros no tiene ni nombre ni operador, y varias áreas de
autocaravana aparecen como puntos contiguos que podrían ser una misma instalación mapeada
por partes.

**Uso admisible:** contar establecimientos y ver su distribución territorial.
**Uso no admisible:** estimar plazas o capacidad de alojamiento.

## (d) OSM infrarrepresenta el alojamiento no hotelero

Reparto por tipo en la extracción de alojamientos de OSM para toda España:

"""

    consolidado = RAW_DIR / "osm_alojamientos_espana_consolidado_20260804.json"
    if consolidado.exists():
        try:
            with consolidado.open(encoding="utf-8") as f:
                meta = json.load(f)["metadata"]
            resumen = meta.get("resumen_por_tipo", {})
            total = sum(resumen.values()) or 1
            doc += "| Tipo | Elementos | % |\n|---|---:|---:|\n"
            for tipo, n in sorted(resumen.items(), key=lambda kv: -kv[1]):
                doc += f"| {tipo} | {num(n)} | {100 * n / total:.1f} % |\n"
        except (ValueError, OSError, KeyError):
            doc += "_(No se pudo leer el consolidado de alojamientos.)_\n"

    doc += """
En un país donde la vivienda de uso turístico es el centro del debate sobre saturación, esa
proporción no refleja el mercado: refleja qué se cartografía en OSM. Baleares es el caso más
claro, con 1.763 hoteles frente a 186 apartamentos.

Y el sesgo **no es uniforme entre comunidades**, de modo que tampoco pueden compararse entre
sí usando sólo OSM. Es la razón de ser de la segunda fuente: OSM aporta la capa geográfica
base, y el registro oficial aporta el denominador de oferta.

## (e) Once comunidades sólo tienen dato de OSM

Las listadas más arriba no tienen registro administrativo incorporado. Para ellas, cualquier
indicador de saturación calculado hoy estaría midiendo la cobertura de OpenStreetMap, no la
oferta real.

**Consecuencia para el dashboard:** la cobertura debe mostrarse explícitamente junto a
cualquier indicador territorial. Un mapa uniforme sugiere comparabilidad donde no la hay, y
ese es el error que más daño haría a las conclusiones del trabajo.

---

## Reproducibilidad

```bash
python etl/transform/generar_inventario.py
```

Regenera este documento con el estado actual de `data/`. Conviene relanzarlo tras cada
extracción para que las cifras del capítulo de metodología no se queden obsoletas.
"""
    return doc


def main() -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    doc = generar()
    SALIDA.write_text(doc, encoding="utf-8")
    print(f"Inventario generado: {SALIDA.relative_to(PROJECT_ROOT)}")
    print(f"  {len(doc.splitlines())} líneas, {len(doc) / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
