"""
Dashboard de inteligencia territorial turística.

Dirigido al **gestor de destino** —administración, DMO, consorcio turístico—, no al
visitante. La pregunta que responde no es "dónde alojarme" sino "dónde hay saturación y
dónde queda margen de inversión".

Principio de diseño que atraviesa toda la aplicación: **ningún número sin su cobertura**.
La calidad del dato es muy desigual entre territorios —cuatro comunidades publican registro
completo de VUT, otras sólo una ciudad, once ninguno— y un mapa uniforme sugeriría
comparabilidad donde no la hay. Por eso el "sin dato" tiene su propio color y nunca se pinta
en la escala de saturación: un municipio sin registro pintado de verde sería una falsa
oportunidad de inversión, que es exactamente el error que este trabajo intenta evitar.

Trabaja en local sobre data/processed/. No usa la base de datos.

Arranque:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import branca.colormap as cm
import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import FastMarkerCluster
from streamlit_folium import st_folium

from recomendaciones import evaluar

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
# Conjunto ligero y autocontenido que genera etl/transform/preparar_datos_dashboard.py. Es
# el único que viaja en el repositorio, y por tanto el que existe en un despliegue. En la
# máquina de desarrollo puede no estar, y entonces se leen los ficheros completos.
DASHBOARD_DIR = PROJECT_ROOT / "data" / "dashboard"


def _fuente(nombre: str) -> Path:
    """Devuelve el fichero de despliegue si existe y, si no, el de trabajo."""
    ligero = DASHBOARD_DIR / nombre
    return ligero if ligero.exists() else PROCESSED_DIR / nombre


INDICADORES = _fuente("indicadores_municipales.csv")
GEOMETRIA = _fuente("municipios_simplificado.geojson")
PUNTOS_LIGEROS = DASHBOARD_DIR / "puntos.csv.gz"

COLOR_SIN_DATO = "#c9ccd1"
LIMITE_PUNTOS = 5_000
SEMILLA = 42

# Mapa base. Se usa el tileset estándar de OpenStreetMap y no el de CARTO: este último
# exige clave de API en producción y, sin ella, sirve teselas con la marca de agua
# "API KEY REQUIRED" repetida sobre todo el fondo del mapa.
TILES_BASE = "OpenStreetMap"

st.set_page_config(
    page_title="Inteligencia territorial turística",
    page_icon="📊",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Vistas del mapa
# ---------------------------------------------------------------------------

VISTAS = {
    "Saturación": {
        "columna": "saturacion_efectiva",
        "etiqueta": "Plazas turísticas por 1.000 habitantes",
        "paleta": ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027"],
        "ayuda": "Verde = margen · Rojo = saturado. Usa plazas VUT + hoteleras donde el "
                 "dato hotelero es fiable, y sólo VUT en el resto.",
        "invertir": False,
    },
    "Oportunidad": {
        "columna": "indice_oportunidad",
        # Verde para el extremo alto, igual que en los otros dos indicadores: el rojo se
        # reserva en toda la aplicación para señalar el problema. Pintar de rojo la mejor
        # oportunidad de inversión contradecía la lectura instintiva del color.
        "paleta": ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850"],
        "etiqueta": "Índice de oportunidad (demanda − saturación)",
        "ayuda": "Verde = alto potencial de inversión: poca oferta pero con demanda, "
                 "servicios y buena conexión. Rojo = ya saturado para la demanda que tiene.",
        "invertir": False,
    },
    "Accesibilidad": {
        "columna": "dist_transporte_km",
        "etiqueta": "Km al nodo de transporte más cercano",
        "paleta": ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027"],
        "ayuda": "Verde = bien conectado. Distancia al aeropuerto, puerto, estación o "
                 "intercambiador más próximo.",
        "invertir": False,
    },
}

# Rotulado de cobertura. Es la traducción a lenguaje de gestor de lo que documenta
# docs/inventario_datos.md.
NOTAS_COBERTURA = {
    "Comunidad de Madrid": "⚠️ Madrid publica **licencias urbanísticas concedidas**, no "
                           "inscripciones en el registro turístico. Sus cifras no son "
                           "comparables con las del resto y quedan fuera de los índices.",
    "Galicia": "ℹ️ Galicia se agrega a **resolución municipal**: su registro no publica "
               "coordenadas utilizables, pero el concello y las plazas sí son fiables.",
    "Cataluña": "⚠️ De Cataluña sólo hay registro de VUT de la **ciudad de Barcelona**. El "
                "resto de municipios aparece como sin dato.",
    "Illes Balears": "⚠️ El registro de VUT cubre **sólo Mallorca**. Menorca y las Pitiusas "
                     "dependen de sus consells y aparecen como sin dato.",
}

CONFIANZA = {
    "registro": ("Registro oficial", "#1a7f37"),
    "no_comparable": ("Mide otra magnitud", "#8250df"),
    "sin_registro": ("Sin registro publicado", "#82071e"),
}


# ---------------------------------------------------------------------------
# Identidad visual
# ---------------------------------------------------------------------------

TUI_AZUL = "#002E5D"
TUI_ROJO = "#D40E14"
FONDO_CLARO = "#F5F7FA"
TEXTO = "#2B3038"
TEXTO_SUAVE = "#5A6472"
BORDE = "#DDE3EA"

# Lectura en palabras de cada extremo de la escala, por indicador. La clave del rediseño:
# sin esto, el usuario no sabe si el verde es deseable o indeseable, y la respuesta cambia
# según el indicador que esté mirando.
# Cada entrada describe los dos extremos de la escala **en el orden en que los pinta la
# paleta**: el primero corresponde al primer color y el segundo al último. Que el texto
# siga al color y no al revés es lo que garantiza que la leyenda no pueda contradecir al
# mapa cuando una paleta se invierte.
LEYENDA_INDICADOR = {
    "Saturación": {
        "primero": ("Margen disponible",
                    "Poca oferta para su población."),
        "ultimo": ("Saturado",
                   "Riesgo de sobrecarga turística."),
        # Sin extremo destacado: la saturación es un diagnóstico, no un objetivo que
        # el gestor persiga. Marcar con una estrella el municipio más saturado sugeriría
        # que es lo que hay que buscar.
        "destacado": None,
        "que_buscar": "El rojo señala los municipios más saturados.",
    },
    "Oportunidad": {
        "primero": ("Poco potencial",
                    "Ya saturado o sin demanda que lo sostenga."),
        "ultimo": ("Alto potencial",
                   "Poca oferta pero con demanda, servicios y buena conexión."),
        "destacado": "ultimo",
        "que_buscar": "Busca el verde intenso: son los municipios con mayor potencial "
                      "de inversión.",
    },
    "Accesibilidad": {
        "primero": ("Bien conectado",
                    "Cerca de aeropuerto, puerto o estación."),
        "ultimo": ("Mal conectado",
                   "Lejos de cualquier nodo de transporte."),
        # Sin extremo destacado, por el mismo motivo que en saturación.
        "destacado": None,
        "que_buscar": "El rojo señala los municipios peor comunicados.",
    },
}


# Qué mide, cómo se lee y de dónde sale cada indicador. Se muestra junto a la leyenda para
# que el usuario no tenga que salir del mapa a buscar la definición.
DESCRIPCION_INDICADOR = {
    "Saturación":
        "Mide la <b>presión turística sobre el territorio</b>: plazas turísticas por cada "
        "1.000 habitantes. Un valor alto indica mucha oferta en relación con la población "
        "residente, señal de posible sobrecarga. Se calcula sobre el registro oficial de "
        "viviendas de uso turístico y, donde hay dato fiable de la Encuesta de Ocupación "
        "Hotelera del INE, también sobre las plazas hoteleras.",
    "Oportunidad":
        "Identifica municipios con <b>potencial de inversión</b>: combina baja saturación "
        "de oferta con presencia de demanda, servicios y buena accesibilidad "
        "(demanda − saturación). Un valor alto señala margen de oferta en una zona que "
        "reúne condiciones para atraer turismo. <b>No es lo mismo que poca saturación</b>: "
        "un municipio sin demanda ni conexión no constituye una oportunidad.",
    "Accesibilidad":
        "Mide la <b>conectividad del municipio</b>: distancia en kilómetros al nodo de "
        "transporte más cercano —aeropuerto, puerto, estación de tren o intercambiador—. "
        "Un valor bajo indica buena conexión. Es un factor determinante del potencial "
        "turístico: la mejor oferta pierde valor si el destino resulta difícil de alcanzar.",
}

# Filtro por tramo. Cada opción indica qué quintiles conserva: los dos superiores, los dos
# inferiores, o todos. Se apoya en los mismos cortes que colorean el mapa.
FILTROS_NIVEL = {
    "Saturación": {
        "Todos": None,
        "Solo saturados (tramo alto)": "alto",
        "Solo con margen (tramo bajo)": "bajo",
    },
    "Oportunidad": {
        "Todos": None,
        "Solo alto potencial (tramo alto)": "alto",
        "Solo bajo potencial (tramo bajo)": "bajo",
    },
    "Accesibilidad": {
        "Todos": None,
        "Solo mal conectados": "alto",
        "Solo bien conectados": "bajo",
    },
}


def aplicar_estilos() -> None:
    """Hoja de estilos corporativa. Sólo presentación: no altera ningún dato."""
    st.markdown(
        f"""
        <style>
        .stApp {{ background: {FONDO_CLARO}; }}
        html, body, [class*="css"] {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         "Helvetica Neue", Arial, sans-serif;
            color: {TEXTO};
        }}
        .block-container {{ padding-top: 1.2rem; max-width: 1400px; }}

        /* Cabecera corporativa */
        .tui-cabecera {{
            background: {TUI_AZUL};
            border-radius: 8px;
            padding: 24px 30px;
            margin-bottom: 22px;
        }}
        .tui-cabecera h1 {{
            color: #fff; font-size: 1.85rem; font-weight: 700;
            margin: 0; letter-spacing: -0.3px; line-height: 1.2;
        }}
        .tui-cabecera p {{
            color: #B9C7D8; margin: 7px 0 0; font-size: 0.95rem;
        }}
        /* Atribución: legible sobre el azul pero claramente por debajo del subtítulo,
           para que no compita con el título. */
        .tui-cabecera .tui-atribucion {{
            color: rgba(255,255,255,0.55); font-size: 0.8rem; margin-top: 10px;
            padding-top: 9px; border-top: 1px solid rgba(255,255,255,0.15);
            letter-spacing: 0.2px;
        }}

        /* Tarjetas */
        .tui-tarjeta {{
            background: #fff; border: 1px solid {BORDE}; border-radius: 8px;
            padding: 16px 18px; height: 100%;
        }}
        .tui-tarjeta h4 {{
            margin: 0 0 8px; font-size: 0.95rem; font-weight: 700; color: {TUI_AZUL};
        }}
        .tui-tarjeta p {{ margin: 0; font-size: 0.88rem; color: {TEXTO_SUAVE}; line-height: 1.5; }}

        /* Tarjetas KPI */
        .tui-kpi {{
            background: #fff; border: 1px solid {BORDE}; border-radius: 8px;
            border-top: 3px solid {TUI_AZUL}; padding: 14px 18px; height: 100%;
        }}
        .tui-kpi .valor {{
            font-size: 1.85rem; font-weight: 700; color: {TUI_AZUL};
            line-height: 1.1; margin: 0;
        }}
        .tui-kpi .etiqueta {{
            font-size: 0.78rem; color: {TEXTO_SUAVE}; text-transform: uppercase;
            letter-spacing: 0.6px; font-weight: 600; margin: 4px 0 0;
        }}
        .tui-kpi .nota {{ font-size: 0.78rem; color: {TEXTO_SUAVE}; margin: 4px 0 0; }}
        .tui-kpi.acento {{ border-top-color: {TUI_ROJO}; }}
        .tui-kpi.acento .valor {{ color: {TUI_ROJO}; }}
        .tui-kpi.neutro {{ border-top-color: #9AA4B2; }}
        .tui-kpi.neutro .valor {{ color: {TEXTO_SUAVE}; }}

        /* Pestañas */
        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 2px solid {BORDE}; }}
        .stTabs [data-baseweb="tab"] {{
            font-size: 1rem; font-weight: 600; color: {TEXTO_SUAVE};
            padding: 10px 18px; border-radius: 6px 6px 0 0;
        }}
        .stTabs [aria-selected="true"] {{
            color: {TUI_AZUL} !important; background: #fff;
            border-bottom: 3px solid {TUI_ROJO};
        }}

        /* Selector de indicador */
        div[data-testid="stSegmentedControl"] button {{
            font-size: 1rem; font-weight: 600; padding: 10px 22px;
        }}

        h2, h3 {{ color: {TUI_AZUL}; font-weight: 700; }}
        .tui-seccion {{ margin-top: 26px; }}

        /* Franja de leyenda del mapa */
        .tui-leyenda {{
            display: flex; gap: 0; border-radius: 6px; overflow: hidden;
            border: 1px solid {BORDE}; margin: 6px 0 4px;
        }}
        .tui-leyenda div {{
            flex: 1; padding: 9px 12px; font-size: 0.82rem; background: #fff;
        }}
        .tui-leyenda .muestra {{
            display: inline-block; width: 13px; height: 13px; border-radius: 3px;
            margin-right: 7px; vertical-align: -2px; border: 1px solid rgba(0,0,0,.15);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def cabecera() -> None:
    st.markdown(
        f"""
        <div class="tui-cabecera">
          <h1>Inteligencia Territorial Turística de España</h1>
          <p>Saturación y oportunidad de inversión, municipio a municipio ·
             Herramienta de apoyo a la gestión de destinos</p>
          <div class="tui-atribucion">
            Trabajo de Fin de Máster · Proyecto basado en un challenge de
            TUI Care Foundation
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def tarjeta_kpi(valor: str, etiqueta: str, nota: str = "", estilo: str = "") -> str:
    clase = f"tui-kpi {estilo}".strip()
    html = f"<div class='{clase}'><p class='valor'>{valor}</p><p class='etiqueta'>{etiqueta}</p>"
    if nota:
        html += f"<p class='nota'>{nota}</p>"
    return html + "</div>"


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

# --- Refinamiento de la lectura de la oportunidad ---
#
# El índice base no se toca: se enriquece con dos clasificaciones que distinguen
# situaciones que la fórmula, por construcción, no puede separar.
#
# Con cero plazas registradas la saturación cae siempre al suelo del percentil, de modo
# que `oportunidad = demanda − constante`: el ranking pasa a ser el de demanda entre los
# municipios sin oferta. Y la demanda se apoya sobre todo en los servicios por habitante,
# que con poblaciones diminutas se disparan —Tollos tiene 32 habitantes y 3 servicios, lo
# que da 93,75 por cada mil—. Ninguna de las dos cosas es un error del cálculo, pero ambas
# exigen advertirse antes de leer el resultado como una recomendación de inversión.

# Plazas por debajo de las cuales se considera que no hay mercado que ampliar.
UMBRAL_OFERTA_MINIMA = 10

# Población por debajo de la cual el indicador se apoya en un denominador demasiado
# pequeño. Se elige 500 porque es donde la mediana de servicios por habitante deja de
# crecer de forma abrupta (10,2 por debajo de 200 hab; 7,7 entre 200 y 500; 4,9 entre 500
# y 1.000; y ya sin saltos a partir de ahí). Marca al 15 % de los municipios.
UMBRAL_POBLACION_FIABLE = 500

TIPOS_OPORTUNIDAD = {
    "crecimiento": ("Oportunidad de crecimiento",
                    "Existe oferta turística y margen para ampliarla: hay mercado.",
                    "#1a7f37"),
    "creacion": ("Oportunidad de creación desde cero",
                 "Sin oferta registrada, pero con señales de demanda y accesibilidad. "
                 "Implica crear mercado donde no lo hay: mayor riesgo y una decisión "
                 "estratégica distinta.",
                 "#0969da"),
}


@st.cache_data(show_spinner=False)
def cargar_indicadores() -> pd.DataFrame:
    df = pd.read_csv(INDICADORES, dtype={"codigo_ine": str})
    df["codigo_ine"] = df["codigo_ine"].str.zfill(5)

    # Saturación efectiva: la total donde el dato hotelero es fiable, y sólo VUT en el
    # resto. Se guarda aparte de qué se usó, para poder decirlo en el mapa.
    df["saturacion_efectiva"] = df["saturacion_total_1000hab"].where(
        df["saturacion_total_1000hab"].notna(), df["saturacion_plazas_1000hab"]
    )
    df["base_saturacion"] = np.where(
        df["saturacion_total_1000hab"].notna(), "VUT + hotelera",
        np.where(df["saturacion_plazas_1000hab"].notna(), "sólo VUT", "sin dato"),
    )

    # --- Clasificación de la oportunidad ---
    plazas = pd.to_numeric(df["plazas_vut"], errors="coerce").fillna(0)
    hoteleras = pd.to_numeric(df.get("plazas_hoteleras"), errors="coerce").fillna(0)
    oferta = plazas + hoteleras
    df["oferta_total_plazas"] = oferta

    df["tipo_oportunidad"] = np.where(
        df["indice_oportunidad"].isna(), None,
        np.where(oferta > UMBRAL_OFERTA_MINIMA, "crecimiento", "creacion"),
    )

    # Señal débil: el indicador se sostiene sobre un denominador demasiado pequeño para
    # ser estable. No se excluye a estos municipios, se advierte de ellos.
    df["senal_debil"] = df["poblacion"] < UMBRAL_POBLACION_FIABLE
    return df


@st.cache_data(show_spinner=False)
def cargar_geometria() -> dict:
    with GEOMETRIA.open(encoding="utf-8") as f:
        geo = json.load(f)
    for elemento in geo.get("features", []):
        props = elemento.get("properties", {})
        if "codigo_ine" in props:
            props["codigo_ine"] = str(props["codigo_ine"]).zfill(5)
    return geo


@st.cache_data(show_spinner=False)
def cargar_puntos() -> pd.DataFrame:
    """
    Capas con dato de punto, para la vista de detalle geográfico.

    Si existe el fichero consolidado de despliegue se usa ése: recorrer los JSON originales
    supone leer 145 MB repartidos en un centenar de ficheros, lo que en local es aceptable
    pero en un servidor de despliegue no.
    """
    import glob

    if PUNTOS_LIGEROS.exists():
        return pd.read_csv(PUNTOS_LIGEROS, compression="gzip")

    capas = {
        "alojamientos": ("alojamientos", "Alojamientos (OSM)"),
        "restauracion": ("restauracion", "Restauración (OSM)"),
        "atracciones": ("atracciones", "Atracciones (OSM)"),
        "transporte_principales": ("transporte_principales", "Transporte (OSM)"),
        "camping": ("camping", "Camping"),
    }
    filas = []
    for clave, (prefijo, etiqueta) in capas.items():
        ficheros = [Path(f) for f in glob.glob(str(RAW_DIR / f"osm_{prefijo}_*.json"))
                    if "consolidado" not in f]
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
                filas.append({"capa": etiqueta, "lat": lat, "lon": lon,
                              "nombre": el.get("tags", {}).get("name")})

    for fichero in sorted(PROCESSED_DIR.glob("vut_normalizado_*.csv")):
        try:
            df = pd.read_csv(fichero, usecols=lambda c: c in ("lat", "lon", "nombre"),
                             low_memory=False)
        except (ValueError, OSError):
            continue
        df = df[df["lat"].notna() & df["lon"].notna()]
        for r in df.itertuples(index=False):
            filas.append({"capa": "VUT registro oficial", "lat": r.lat, "lon": r.lon,
                          "nombre": getattr(r, "nombre", None)})

    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Utilidades de presentación
# ---------------------------------------------------------------------------

def fmt(valor, decimales: int = 0, sufijo: str = "") -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "sin dato"
    try:
        texto = f"{float(valor):,.{decimales}f}".replace(",", "·").replace(".", ",")
        return texto.replace("·", ".") + sufijo
    except (TypeError, ValueError):
        return str(valor)


def insignia_confianza(cobertura: str) -> str:
    etiqueta, color = CONFIANZA.get(cobertura, ("Desconocida", "#57606a"))
    return f"<span style='color:{color};font-weight:600'>{etiqueta}</span>"


# ---------------------------------------------------------------------------
# 1. Vista principal
# ---------------------------------------------------------------------------

def escala_quintiles(serie: pd.Series) -> list[float]:
    """
    Cortes de los quintiles de la serie, sin duplicados.

    Se aísla en una función porque la usan tanto el coloreado del mapa como la leyenda, y
    tienen que coincidir exactamente: si divergieran, la leyenda anunciaría unos tramos y
    el mapa pintaría otros.
    """
    cortes = sorted(set(serie.quantile([0, .2, .4, .6, .8, 1]).tolist()))
    if len(cortes) < 3:
        cortes = sorted({serie.min(), serie.max()})
    if len(cortes) < 2:
        cortes = [cortes[0], cortes[0] + 1]
    return cortes


def colores_por_tramo(paleta: list[str], n_tramos: int) -> list[str]:
    """
    Un color por tramo, repartidos uniformemente a lo largo de la paleta.

    Es lo que distingue una escala por cuantiles de una lineal. `LinearColormap.to_step()`
    sigue interpolando el color según el valor dentro del rango total, de modo que con una
    distribución de cola larga los tramos bajos acaparan casi toda la gama: un municipio
    del cuarto quintil de saturación se pintaba de verde porque su valor absoluto seguía
    siendo pequeño frente al máximo. Asignando un color por tramo, el color pasa a
    expresar la posición relativa, que es lo que se quiere comunicar.
    """
    if n_tramos <= 1:
        return [paleta[-1]]
    rampa = cm.LinearColormap(paleta, vmin=0, vmax=1)
    # `rgb_hex_str` y no `rampa(...)`: la llamada directa devuelve el color con canal alfa
    # (#RRGGBBAA) y StepColormap sólo admite seis dígitos.
    return [rampa.rgb_hex_str(i / (n_tramos - 1)) for i in range(n_tramos)]


def escala_mapa(conf: dict, cortes: list[float]) -> cm.StepColormap:
    """Escala discreta del mapa: un color fijo por tramo de cuantil."""
    return cm.StepColormap(
        colores_por_tramo(conf["paleta"], len(cortes) - 1),
        index=cortes, vmin=min(cortes), vmax=max(cortes),
    )


def tramos_quintiles(vista: str, conf: dict, serie: pd.Series) -> str:
    """Franja con un bloque por quintil, su color y el rango de valores que abarca."""
    cortes = escala_quintiles(serie)
    colores = colores_por_tramo(conf["paleta"], len(cortes) - 1)
    unidad = "km" if vista == "Accesibilidad" else ""

    bloques = ""
    for i in range(len(cortes) - 1):
        desde, hasta = cortes[i], cortes[i + 1]
        color = colores[i]
        pct_desde = round(100 * i / (len(cortes) - 1))
        pct_hasta = round(100 * (i + 1) / (len(cortes) - 1))
        bloques += (
            f"<div style='flex:1;text-align:center'>"
            f"<div style='height:14px;background:{color};border-radius:3px 3px 0 0'></div>"
            f"<div style='font-size:0.72rem;color:{TEXTO_SUAVE};padding:3px 2px'>"
            f"{fmt(desde, 1)}–{fmt(hasta, 1)} {unidad}<br>"
            f"<span style='opacity:.75'>{pct_desde}–{pct_hasta} %</span></div></div>"
        )

    return (
        f"<div style='margin-top:10px'>"
        f"<div style='font-size:0.78rem;color:{TEXTO_SUAVE};margin-bottom:4px'>"
        f"Tramos por <b>quintiles</b>: cada bloque agrupa a la quinta parte de los "
        f"municipios con dato. Debajo, el rango de valores y el percentil.</div>"
        f"<div style='display:flex;gap:3px'>{bloques}</div></div>"
    )


def leyenda_contextual(vista: str, conf: dict, serie: pd.Series | None = None) -> None:
    """
    Leyenda del indicador **activo**, y sólo de ése.

    Mostrar los tres a la vez obligaba al lector a averiguar cuál le aplicaba, y como el
    color favorable no es el mismo en los tres —lo deseable en saturación es poca oferta,
    en accesibilidad poca distancia y en oportunidad mucho margen—, la comparación
    simultánea inducía justo el error que la leyenda debe evitar. Este bloque se reescribe
    entero al cambiar de indicador.

    Los colores se leen de la propia paleta del mapa, de modo que si una paleta se invierte
    la leyenda la sigue automáticamente y no puede quedar desfasada.
    """
    textos = LEYENDA_INDICADOR[vista]
    color_primero, color_ultimo = conf["paleta"][0], conf["paleta"][-1]
    destacado = textos["destacado"]

    def celda(clave: str, color: str) -> str:
        titulo, detalle = textos[clave]
        # Sólo se destaca cuando el indicador tiene un extremo que el gestor persigue.
        # En saturación y accesibilidad ambos extremos son diagnóstico —el rojo describe
        # un problema, no un objetivo—, así que ninguna celda lleva estrella ni resalte.
        es_objetivo = destacado is not None and clave == destacado
        resalte = (f"border:2px solid {TUI_AZUL};background:#F0F4F9;"
                   if es_objetivo else f"border:1px solid {BORDE};background:#fff;")
        marca = " ⭐" if es_objetivo else ""
        return (
            f"<div style='flex:1;padding:12px 14px;border-radius:6px;{resalte}'>"
            f"<span class='muestra' style='background:{color};width:15px;height:15px'></span>"
            f"<b>{titulo}</b>{marca}<br>"
            f"<span style='color:{TEXTO_SUAVE};font-size:0.85rem'>{detalle}</span></div>"
        )

    st.markdown(
        f"<div style='margin:10px 0 4px'>"
        f"<div style='font-size:0.82rem;text-transform:uppercase;letter-spacing:0.6px;"
        f"color:{TEXTO_SUAVE};font-weight:700;margin-bottom:6px'>Estás viendo</div>"
        f"<div style='font-size:1.25rem;font-weight:700;color:{TUI_AZUL};"
        f"margin-bottom:8px'>{vista}</div>"
        f"<div style='background:#fff;border:1px solid {BORDE};border-left:4px solid "
        f"{TUI_AZUL};border-radius:6px;padding:11px 15px;margin-bottom:12px;"
        f"font-size:0.88rem;line-height:1.55;color:{TEXTO}'>"
        f"{DESCRIPCION_INDICADOR.get(vista, '')}</div>"
        f"<div style='display:flex;gap:10px;flex-wrap:wrap'>"
        f"{celda('primero', color_primero)}"
        f"{celda('ultimo', color_ultimo)}"
        f"<div style='flex:1;padding:12px 14px;border-radius:6px;"
        f"border:1px solid {BORDE};background:#fff'>"
        f"<span class='muestra' style='background:{COLOR_SIN_DATO};width:15px;height:15px'>"
        f"</span><b>Sin dato</b><br>"
        f"<span style='color:{TEXTO_SUAVE};font-size:0.85rem'>La comunidad no publica "
        f"registro. <b>No es saturación baja.</b></span></div>"
        f"</div>"
        # La franja es una instrucción de búsqueda sólo donde hay algo que buscar; en el
        # resto es una descripción del diagnóstico, y el icono lo refleja.
        f"<div style='margin-top:8px;padding:9px 14px;border-radius:6px;"
        f"background:{TUI_AZUL};color:#fff;font-size:0.9rem'>"
        f"{'👉' if destacado else 'ℹ️'} {textos['que_buscar']}</div>"
        + (tramos_quintiles(vista, conf, serie) if serie is not None and len(serie) else "")
        + f"</div>",
        unsafe_allow_html=True,
    )


def preparar_tabla(seleccion: pd.DataFrame, vista: str, conf: dict,
                   columna: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Construye la tabla de municipios y la lista de códigos INE en el mismo orden.

    Devolver ambas cosas juntas es lo que permite traducir la fila que el usuario clica
    —que Streamlit identifica por su posición— al municipio concreto que representa.
    """
    # La columna de confianza dice de dónde sale el dato, que en este proyecto es tan
    # relevante como el propio valor.
    if vista == "Saturación":
        confianza = seleccion["base_saturacion"]
    else:
        confianza = seleccion["cobertura_vut"].map(
            lambda c: CONFIANZA.get(c, ("—", ""))[0]
        )

    tabla = pd.DataFrame({
        "codigo_ine": seleccion["codigo_ine"],
        "Municipio": seleccion["nombre"],
        "Provincia": seleccion["provincia"],
        conf["etiqueta"]: seleccion[columna].round(2),
        "Población": seleccion["poblacion"],
        "Base del dato": confianza,
    }).sort_values(conf["etiqueta"], ascending=False)

    codigos = tabla["codigo_ine"].tolist()
    return tabla.drop(columns="codigo_ine"), codigos


def vista_principal(df: pd.DataFrame, geo: dict) -> None:
    st.markdown("### Mapa nacional")

    izq, cen, der = st.columns([3, 2, 2])
    with izq:
        vista = st.segmented_control(
            "Indicador", list(VISTAS), default="Saturación", key="vista_mapa",
        ) or "Saturación"
    conf = VISTAS[vista]
    columna = conf["columna"]

    with cen:
        ccaa_sel = st.multiselect(
            "Comunidades", sorted(df["ccaa"].dropna().unique()),
            default=[], key="ccaa_mapa", help="Vacío = toda España.",
        )
    with der:
        opciones_nivel = FILTROS_NIVEL[vista]
        nivel_sel = st.selectbox(
            "Nivel en el indicador", list(opciones_nivel), key=f"nivel_{vista}",
            help="Usa los mismos tramos por percentiles que colorean el mapa. Los "
                 "municipios sin dato quedan siempre fuera de este filtro.",
        )

    datos = df[df["ccaa"].isin(ccaa_sel)] if ccaa_sel else df
    con_dato = datos[datos[columna].notna()]

    # --- Filtro por tramo ---
    # Los cortes se calculan sobre el ámbito ya filtrado por comunidad, de modo que "tramo
    # alto" significa alto dentro de lo que se está viendo. Se aplica sólo a los municipios
    # con dato: los que no lo tienen no pertenecen a ningún tramo y siguen en gris.
    nivel = opciones_nivel[nivel_sel]
    seleccion = con_dato
    if nivel and not con_dato.empty:
        cortes_nivel = escala_quintiles(con_dato[columna])
        if len(cortes_nivel) >= 3:
            if nivel == "alto":
                umbral = cortes_nivel[-3]  # dos quintiles superiores
                seleccion = con_dato[con_dato[columna] > umbral]
            else:
                umbral = cortes_nivel[2]   # dos quintiles inferiores
                seleccion = con_dato[con_dato[columna] <= umbral]

    leyenda_contextual(vista, conf, con_dato[columna] if not con_dato.empty else None)
    st.caption(conf["ayuda"])

    # --- Métricas de cobertura, siempre visibles ---
    c1, c2, c3, c4 = st.columns(4)
    pct = f"{100 * len(con_dato) / len(datos):.0f} % del ámbito" if len(datos) else "—"
    c1.markdown(tarjeta_kpi(fmt(len(datos)), "Municipios en el ámbito"),
                unsafe_allow_html=True)
    c2.markdown(tarjeta_kpi(fmt(len(con_dato)), "Con este indicador", pct),
                unsafe_allow_html=True)
    c3.markdown(tarjeta_kpi(fmt(len(datos) - len(con_dato)), "Sin dato",
                            "Se pintan en gris", "neutro"), unsafe_allow_html=True)
    if vista == "Saturación":
        fiables = int((datos["base_saturacion"] == "VUT + hotelera").sum())
        c4.markdown(tarjeta_kpi(fmt(fiables), "Incluyen hoteles",
                                "VUT + EOH", "acento"), unsafe_allow_html=True)
    else:
        mediana = fmt(con_dato[columna].median(), 1) if len(con_dato) else "—"
        c4.markdown(tarjeta_kpi(mediana, "Mediana", conf["etiqueta"], "acento"),
                    unsafe_allow_html=True)

    st.markdown("<div class='tui-seccion'></div>", unsafe_allow_html=True)

    if con_dato.empty:
        st.warning("No hay datos para esta selección.")
        return

    # Escala por cuantiles: estos indicadores tienen colas muy largas (hay municipios con
    # 1.500 plazas por 1.000 habitantes y la mediana ronda cero). Una escala lineal dejaría
    # el 99 % del mapa del mismo color.
    # Quintiles: cada tramo agrupa a la quinta parte de los municipios con dato, de modo
    # que el color expresa la posición relativa y no el valor absoluto.
    #
    # Con cortes concentrados en la cola alta —el reparto anterior era 0-50-75-90-97-99,5—
    # la mitad de los municipios compartía el color más favorable y sólo el 0,5 % superior
    # alcanzaba el extremo. El resultado era un mapa casi enteramente verde en el que
    # destinos notoriamente saturados como Peñíscola o Benidorm no se distinguían de un
    # pueblo de interior sin oferta.
    #
    # Los municipios sin dato quedan fuera del cálculo: `con_dato` ya los excluye, así que
    # no desplazan los cortes.
    # La escala se usa sólo para colorear los polígonos. No se añade al mapa como barra de
    # leyenda: folium la superpone sobre el lienzo y queda cortada, y además duplicaría la
    # franja de quintiles que ya se muestra arriba con los rangos y los percentiles.
    cortes = escala_quintiles(con_dato[columna])
    escala = escala_mapa(conf, cortes)

    # --- Selección de fila en la tabla ---
    #
    # La tabla se dibuja a la derecha del mapa, pero su selección tiene que conocerse
    # antes de construirlo. Streamlit resuelve esto por sí solo: `on_select="rerun"`
    # provoca una nueva ejecución y el estado del widget queda disponible desde el
    # principio, así que aquí se lee de `session_state` lo que el usuario clicó en la
    # pasada anterior.
    #
    # La clave incluye la firma de los filtros. Streamlit identifica la fila por su
    # posición, de modo que si cambiaran los filtros conservando la clave, el índice
    # guardado apuntaría a un municipio distinto sin que nada fallara. Al variar la clave,
    # el widget se recrea y la selección se descarta.
    tabla, codigos_tabla = preparar_tabla(seleccion, vista, conf, columna)
    firma = f"{vista}|{nivel_sel}|{'-'.join(sorted(ccaa_sel))}|{len(seleccion)}"
    clave_tabla = f"tabla_vista_{firma}"

    estado = st.session_state.get(clave_tabla)
    filas_sel = []
    if estado is not None:
        filas_sel = getattr(getattr(estado, "selection", None), "rows", None) or \
                    (estado.get("selection", {}).get("rows", []) if isinstance(estado, dict) else [])

    codigo_activo = None
    if filas_sel and 0 <= filas_sel[0] < len(codigos_tabla):
        codigo_activo = codigos_tabla[filas_sel[0]]

    valores = dict(zip(con_dato["codigo_ine"], con_dato[columna]))
    # Con un filtro de nivel activo el mapa muestra sólo los municipios del tramo; sin él,
    # todo el ámbito, incluidos los que carecen de dato, que se pintan en gris.
    visibles = set(seleccion["codigo_ine"]) if nivel else set(datos["codigo_ine"])

    # Con un municipio elegido en la tabla, el mapa se centra en su centroide y se acerca;
    # sin selección, mantiene la vista general.
    centro, zoom = [40.0, -3.7], 6
    if codigo_activo:
        fila_activa = seleccion[seleccion["codigo_ine"] == codigo_activo]
        if not fila_activa.empty and pd.notna(fila_activa.iloc[0]["lat_centro"]):
            centro = [float(fila_activa.iloc[0]["lat_centro"]),
                      float(fila_activa.iloc[0]["lon_centro"])]
            zoom = 11

    mapa = folium.Map(location=centro, zoom_start=zoom, tiles=TILES_BASE)

    def estilo(elemento):
        codigo = elemento["properties"].get("codigo_ine")
        if codigo not in visibles:
            return {"fillOpacity": 0, "weight": 0}
        valor = valores.get(codigo)
        # El "sin dato" tiene su propio color y jamás entra en la escala verde-rojo.
        color = COLOR_SIN_DATO if valor is None or pd.isna(valor) else escala(valor)
        if codigo == codigo_activo:
            # El municipio seleccionado conserva su color de indicador y se distingue por
            # un contorno grueso en el azul corporativo.
            return {"fillColor": color, "color": TUI_AZUL, "weight": 3.5,
                    "fillOpacity": 0.9}
        return {"fillColor": color, "color": "#ffffff", "weight": 0.25, "fillOpacity": 0.8}

    # Cifras del popup, adjuntadas a la geometría.
    # El contenido del globo cambia con el indicador: mostrar siempre las cifras de
    # saturación mientras se está mirando accesibilidad obliga a leer un dato que no es el
    # que se está consultando. Nombre, provincia y confianza permanecen en los tres casos.
    campos = ["nombre", "provincia", "poblacion", "plazas_vut", "plazas_hoteleras",
              "saturacion_efectiva", "base_saturacion", "indice_oportunidad",
              "indice_demanda", "indice_saturacion", "servicios_1000hab",
              "n_atracciones", "dist_transporte_km", "n_transporte", "cobertura_vut"]
    campos = [c for c in campos if c in datos.columns]
    # Nombre propio: `tabla` designa el marco que alimenta la lista de municipios.
    datos_popup = datos.set_index("codigo_ine")[campos].to_dict("index")

    for elemento in geo["features"]:
        codigo = elemento["properties"].get("codigo_ine")
        info = datos_popup.get(codigo)
        p = elemento["properties"]
        if info is None:
            p["_nombre"] = p.get("nombre_municipio", "—")
            p["_a"] = p["_b"] = p["_c"] = p["_d"] = "—"
            p["_confianza"] = "Fuera de la selección"
            continue

        p["_nombre"] = f"{info['nombre']} ({info['provincia']})"
        p["_poblacion"] = fmt(info.get("poblacion")) + " hab"
        p["_confianza"] = CONFIANZA.get(info.get("cobertura_vut"), ("—", ""))[0]

        if vista == "Saturación":
            p["_a"] = fmt(info.get("plazas_vut")) + " plazas"
            p["_b"] = fmt(info.get("plazas_hoteleras")) + " plazas"
            p["_c"] = (fmt(info.get("saturacion_efectiva"), 1) + " /1.000 hab"
                       if pd.notna(info.get("saturacion_efectiva")) else "sin dato")
            p["_d"] = info.get("base_saturacion", "—")
        elif vista == "Oportunidad":
            p["_a"] = (fmt(info.get("indice_oportunidad"), 1)
                       if pd.notna(info.get("indice_oportunidad")) else "sin dato")
            p["_b"] = (fmt(info.get("indice_demanda"), 1)
                       if pd.notna(info.get("indice_demanda")) else "sin dato")
            p["_c"] = (fmt(info.get("indice_saturacion"), 1)
                       if pd.notna(info.get("indice_saturacion")) else "sin dato")
            p["_d"] = (f"{fmt(info.get('servicios_1000hab'), 1)} serv./1.000 hab · "
                       f"{fmt(info.get('n_atracciones'))} atracciones")
        else:  # Accesibilidad
            p["_a"] = (fmt(info.get("dist_transporte_km"), 1) + " km"
                       if pd.notna(info.get("dist_transporte_km")) else "sin dato")
            p["_b"] = fmt(info.get("n_transporte")) + " en el término municipal"
            p["_c"] = p["_d"] = "—"

    ETIQUETAS_POPUP = {
        "Saturación": ["Población:", "Plazas VUT:", "Plazas hoteleras:", "Saturación:",
                       "Base del cálculo:", "Confianza del dato:"],
        "Oportunidad": ["Población:", "Índice de oportunidad:", "Componente demanda:",
                        "Componente saturación:", "Servicios y recursos:",
                        "Confianza del dato:"],
        "Accesibilidad": ["Población:", "Al nodo más cercano:", "Nodos de transporte:",
                          "Confianza del dato:"],
    }
    # Accesibilidad sólo tiene dos datos propios, así que no usa las ranuras _c y _d.
    if vista == "Accesibilidad":
        campos_popup = ["_nombre", "_poblacion", "_a", "_b", "_confianza"]
    else:
        campos_popup = ["_nombre", "_poblacion", "_a", "_b", "_c", "_d", "_confianza"]
    alias_popup = [""] + ETIQUETAS_POPUP[vista]

    folium.GeoJson(
        geo,
        style_function=estilo,
        highlight_function=lambda _: {"weight": 2, "color": "#111111"},
        tooltip=folium.GeoJsonTooltip(
            fields=campos_popup,
            aliases=alias_popup,
            sticky=True,
            labels=True,
        ),
        smooth_factor=1.0,
    ).add_to(mapa)

    # Mapa y tabla lado a lado, alimentados por la misma selección: así se ve dónde está
    # el fenómeno y qué municipios lo componen sin cambiar de vista. En pantallas estrechas
    # Streamlit apila las columnas por sí solo.
    col_mapa, col_tabla = st.columns([1, 1], gap="medium")

    with col_mapa:
        if codigo_activo:
            fila_activa = seleccion[seleccion["codigo_ine"] == codigo_activo]
            if not fila_activa.empty:
                f = fila_activa.iloc[0]
                st.markdown(
                    f"<div style='background:{TUI_AZUL};color:#fff;padding:8px 14px;"
                    f"border-radius:6px;margin-bottom:8px;font-size:0.9rem'>"
                    f"📍 <b>{f['nombre']}</b> ({f['provincia']}) · "
                    f"{conf['etiqueta']}: <b>{fmt(f[columna], 2)}</b>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        # `key` fijo para el mapa: sin él, st_folium reinicia la vista en cada rerun y el
        # zoom sobre el municipio elegido se perdería al instante.
        st_folium(mapa, width=None, height=560, returned_objects=[],
                  key=f"mapa_{firma}_{codigo_activo or 'general'}")

    with col_tabla:
        st.markdown(
            f"<div style='font-weight:700;color:{TUI_AZUL};font-size:1rem;"
            f"margin-bottom:2px'>Municipios mostrados</div>"
            f"<div style='color:{TEXTO_SUAVE};font-size:0.83rem;margin-bottom:8px'>"
            f"{len(seleccion):,} municipios · ordenados por {conf['etiqueta'].lower()} · "
            f"<b>clica una fila para localizarlo en el mapa</b>"
            f"</div>".replace(",", "."),
            unsafe_allow_html=True,
        )
        if seleccion.empty:
            st.info("Ningún municipio cumple los filtros seleccionados.")
        else:
            st.dataframe(
                tabla, hide_index=True, height=496, width="stretch",
                key=clave_tabla, on_select="rerun", selection_mode="single-row",
                column_config={
                    "Población": st.column_config.NumberColumn(format="%d"),
                    conf["etiqueta"]: st.column_config.NumberColumn(format="%.2f"),
                },
            )
            if codigo_activo:
                if st.button("Volver a la vista general", width="stretch"):
                    # Vaciar la selección del widget devuelve el mapa a su encuadre inicial.
                    st.session_state.pop(clave_tabla, None)
                    st.rerun()

    # La leyenda de colores ya se muestra sobre el mapa, contextualizada al indicador
    # activo. Aquí sólo quedan los avisos de cobertura por territorio.
    for ccaa, nota in NOTAS_COBERTURA.items():
        if not ccaa_sel or ccaa in ccaa_sel:
            st.caption(nota)


# ---------------------------------------------------------------------------
# 2. Rankings
# ---------------------------------------------------------------------------

def rankings(df: pd.DataFrame) -> None:
    st.subheader("Rankings municipales")

    c1, c2, c3 = st.columns([2, 2, 1])
    ccaa_sel = c1.multiselect("Comunidades", sorted(df["ccaa"].dropna().unique()),
                              default=[], key="ccaa_rank")
    solo_fiable = c2.selectbox(
        "Nivel de confianza",
        ["Sólo registro oficial", "Sólo con dato hotelero (VUT + EOH)", "Todos"],
        key="conf_rank",
    )
    poblacion_min = c3.number_input("Población mínima", 0, 100_000, 1_000, step=500,
                                    key="pob_rank")

    datos = df.copy()
    if ccaa_sel:
        datos = datos[datos["ccaa"].isin(ccaa_sel)]
    datos = datos[datos["poblacion"].fillna(0) >= poblacion_min]
    if solo_fiable == "Sólo registro oficial":
        datos = datos[datos["cobertura_vut"] == "registro"]
    elif solo_fiable == "Sólo con dato hotelero (VUT + EOH)":
        datos = datos[datos["saturacion_total_1000hab"].notna()]

    st.caption(
        f"{len(datos):,} municipios cumplen el filtro. ".replace(",", ".")
        + "«Sólo registro oficial» excluye las comunidades sin registro publicado y Madrid, "
          "que mide licencias urbanísticas."
    )

    columnas = {
        "nombre": "Municipio", "provincia": "Provincia", "poblacion": "Población",
        "plazas_vut": "Plazas VUT", "plazas_hoteleras": "Plazas hotel",
        "saturacion_efectiva": "Saturación /1.000 hab", "base_saturacion": "Base",
        "indice_riesgo": "Riesgo", "indice_oportunidad": "Oportunidad",
    }

    izq, der = st.columns(2)
    with izq:
        st.markdown("#### Top 20 · más saturados")
        top = datos.nlargest(20, "indice_riesgo")[list(columnas)]
        st.dataframe(top.rename(columns=columnas), hide_index=True,
                     width="stretch")
    with der:
        st.markdown("#### Top 20 · más oportunidad")
        top = datos.nlargest(20, "indice_oportunidad")[list(columnas)]
        st.dataframe(top.rename(columns=columnas), hide_index=True,
                     width="stretch")

    with st.expander("Cómo se construyen estos índices"):
        st.markdown(
            """
**Riesgo** = 65 % saturación + 35 % demanda. Saturación combina plazas por habitante
(60 %) y plazas por km² (40 %), ambas como percentil nacional.

**Oportunidad** = demanda − saturación. La demanda pondera servicios por habitante (40 %),
atracciones por habitante (25 %), densidad de servicios por km² (20 %) y accesibilidad
(15 %).

Los servicios se miden **por habitante** y no sólo por km² a propósito: la densidad por km²
mide urbanidad, no turismo, y con ella los barrios dormitorio de las áreas metropolitanas
encabezaban el índice de oportunidad.

Ambos índices requieren conocer la saturación, así que **no se calculan** donde no hay
registro de VUT ni donde el dato mide otra magnitud.
            """
        )


# ---------------------------------------------------------------------------
# 3. Ficha de municipio
# ---------------------------------------------------------------------------

ETIQUETA_CONFIANZA = {
    "alta": ("Confianza alta", "#1a7f37"),
    "media": ("Confianza media", "#bf8700"),
    "insuficiente": ("Datos insuficientes", "#6e7781"),
}


def _lectura_automatica(fila: pd.Series, df: pd.DataFrame) -> None:
    """
    Lectura del municipio generada por reglas sobre los indicadores.

    Se muestra siempre acompañada de la evidencia que la sustenta y de su nivel de confianza:
    una recomendación de inversión sin la cobertura del dato que la respalda es exactamente
    el tipo de conclusión que este sistema pretende evitar.
    """
    rec = evaluar(fila, df)
    etiqueta, color_conf = ETIQUETA_CONFIANZA.get(rec.confianza, ("—", "#6e7781"))

    st.markdown(
        f"<div style='border-left:5px solid {rec.color};background:#f6f8fa;"
        f"padding:14px 18px;border-radius:4px;margin:12px 0'>"
        f"<div style='display:flex;align-items:baseline;gap:12px;flex-wrap:wrap'>"
        f"<span style='font-size:1.15rem;font-weight:700;color:{rec.color}'>{rec.titulo}</span>"
        f"<span style='color:{color_conf};font-weight:600;font-size:0.85rem'>{etiqueta}</span>"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    st.markdown(f"**Diagnóstico.** {rec.diagnostico}")
    st.markdown(rec.recomendacion)

    for aviso in rec.avisos:
        st.warning(aviso, icon="⚠️")

    with st.expander("En qué datos se basa esta lectura"):
        for evidencia in rec.evidencias:
            st.markdown(f"- {evidencia}")
        st.caption(
            "Lectura generada por reglas deterministas sobre percentiles nacionales, sin "
            "modelos predictivos. Los umbrales y la clasificación están documentados en "
            "`dashboard/recomendaciones.py`."
        )


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------

# Percentil a partir del cual una situación se considera destacable.
P_ALERTA = 90
# Para la alerta combinada se usa un umbral algo más bajo en cada eje: lo que la hace
# relevante es la coincidencia de los dos factores, no que cada uno sea extremo.
P_ALERTA_COMBINADA = 75


def alertas(df: pd.DataFrame) -> None:
    st.markdown("### Alertas territoriales")
    st.caption(
        "Municipios que cumplen condiciones de riesgo u oportunidad destacable según los "
        "indicadores calculados. Los umbrales son percentiles sobre el conjunto de "
        "municipios con dato."
    )

    st.markdown(
        f"<div style='background:#fff8c5;border-left:5px solid #bf8700;"
        f"padding:11px 15px;border-radius:6px;margin:8px 0 16px;font-size:0.9rem'>"
        f"<b>Las alertas sólo se calculan sobre municipios con dato fiable.</b> Quedan "
        f"fuera los {int((df['origen_vut'] == 'sin_dato').sum()):,} municipios cuya "
        f"comunidad no publica registro de viviendas de uso turístico y los que miden otra "
        f"magnitud. Su ausencia en esta lista no significa que no tengan presión "
        f"turística: significa que no hay con qué medirla.".replace(",", ".")
        + "</div>",
        unsafe_allow_html=True,
    )

    ccaa_sel = st.multiselect(
        "Filtrar comunidades", sorted(df["ccaa"].dropna().unique()),
        default=[], key="ccaa_alertas", help="Vacío = toda España.",
    )

    # Los percentiles se calculan siempre sobre el conjunto nacional con dato: una alerta
    # de saturación crítica debe significar lo mismo en Galicia que en Andalucía. El filtro
    # de comunidad se aplica después, sólo para mostrar.
    fiable = (df["origen_vut"] != "sin_dato") & (df["cobertura_vut"] != "no_comparable")
    base = df[fiable].copy()
    base["p_saturacion"] = base["saturacion_efectiva"].rank(pct=True) * 100
    base["p_oportunidad"] = base["indice_oportunidad"].rank(pct=True) * 100
    base["p_distancia"] = base["dist_transporte_km"].rank(pct=True) * 100

    ambito = base[base["ccaa"].isin(ccaa_sel)] if ccaa_sel else base

    categorias = [
        {
            "clave": "critica",
            "titulo": "🔴 Saturación crítica",
            "color": "#d73027",
            "explica": "Oferta turística en el tramo más alto del país en relación con su "
                       "población residente. Riesgo de sobrecarga sobre servicios y "
                       "vivienda.",
            "filtro": lambda d: d[d["p_saturacion"] > P_ALERTA],
            "orden": "p_saturacion",
            "motivo": lambda f: (
                f"Saturación de {fmt(f['saturacion_efectiva'], 1)} plazas por 1.000 "
                f"habitantes (percentil {fmt(f['p_saturacion'], 0)})."
            ),
            "columnas": ["saturacion_efectiva", "p_saturacion"],
            "etiquetas": ["Plazas / 1.000 hab", "Percentil"],
        },
        {
            "clave": "crecimiento",
            "titulo": "🟢 Oportunidad de crecimiento",
            "color": "#1a7f37",
            "explica": "Municipios con oferta turística ya existente y margen para "
                       "ampliarla: hay mercado que escalar. Es la categoría con menor "
                       "riesgo de las dos de oportunidad.",
            "filtro": lambda d: d[(d["p_oportunidad"] > P_ALERTA)
                                  & (d["tipo_oportunidad"] == "crecimiento")],
            "orden": "p_oportunidad",
            "motivo": lambda f: (
                f"Índice de oportunidad de {fmt(f['indice_oportunidad'], 1)} "
                f"(percentil {fmt(f['p_oportunidad'], 0)}) con "
                f"{fmt(f['oferta_total_plazas'], 0)} plazas ya registradas."
            ),
            "columnas": ["indice_oportunidad", "p_oportunidad", "oferta_total_plazas"],
            "etiquetas": ["Índice oportunidad", "Percentil", "Plazas actuales"],
        },
        {
            "clave": "creacion",
            "titulo": "🔵 Oportunidad de creación desde cero",
            "color": "#0969da",
            "explica": "Municipios sin oferta registrada pero con señales de demanda, "
                       "servicios y accesibilidad. Supone crear mercado donde no lo hay: "
                       "mayor riesgo y una decisión estratégica distinta a la de ampliar "
                       "una oferta existente.",
            "filtro": lambda d: d[(d["p_oportunidad"] > P_ALERTA)
                                  & (d["tipo_oportunidad"] == "creacion")],
            "orden": "p_oportunidad",
            "motivo": lambda f: (
                f"Índice de oportunidad de {fmt(f['indice_oportunidad'], 1)} "
                f"(percentil {fmt(f['p_oportunidad'], 0)}) sin oferta registrada"
                + (". Señal débil: población por debajo de "
                   f"{UMBRAL_POBLACION_FIABLE} habitantes."
                   if f["senal_debil"] else ".")
            ),
            "columnas": ["indice_oportunidad", "p_oportunidad", "oferta_total_plazas"],
            "etiquetas": ["Índice oportunidad", "Percentil", "Plazas actuales"],
        },
        {
            "clave": "combinada",
            "titulo": "🟠 Saturación alta con baja accesibilidad",
            "color": "#bf8700",
            "explica": "Presión turística elevada en un municipio mal comunicado. La "
                       "combinación agrava la presión: la carga llega por carretera y los "
                       "servicios de apoyo quedan lejos.",
            "filtro": lambda d: d[(d["p_saturacion"] > P_ALERTA_COMBINADA)
                                  & (d["p_distancia"] > P_ALERTA_COMBINADA)],
            "orden": "p_saturacion",
            "motivo": lambda f: (
                f"Saturación en el percentil {fmt(f['p_saturacion'], 0)} y "
                f"{fmt(f['dist_transporte_km'], 1)} km al nodo de transporte más cercano "
                f"(percentil {fmt(f['p_distancia'], 0)} de lejanía)."
            ),
            "columnas": ["saturacion_efectiva", "dist_transporte_km", "p_saturacion"],
            "etiquetas": ["Plazas / 1.000 hab", "Km al transporte", "Percentil saturación"],
        },
    ]

    st.markdown(
        f"<div style='background:#fff;border:1px solid {BORDE};border-left:4px solid "
        f"{TUI_AZUL};border-radius:6px;padding:11px 15px;margin-bottom:14px;"
        f"font-size:0.88rem;line-height:1.55'>"
        f"<b>Sobre la oportunidad.</b> Se separa en dos categorías porque la fórmula no "
        f"puede distinguirlas: un municipio sin oferta registrada obtiene siempre la "
        f"saturación mínima, de modo que su índice equivale a su demanda. Ampliar una "
        f"oferta que ya funciona y crearla donde no existe son decisiones de riesgo "
        f"distinto. Además se marca como <b>señal débil</b> a los municipios de menos de "
        f"{UMBRAL_POBLACION_FIABLE} habitantes: sus indicadores por habitante se apoyan en "
        f"un denominador tan pequeño que basta un bar para situarlos en el percentil más "
        f"alto del país.</div>",
        unsafe_allow_html=True,
    )

    resumen = st.columns(len(categorias))
    conteos = {}
    for col, cat in zip(resumen, categorias):
        n = len(cat["filtro"](ambito).dropna(subset=[cat["orden"]]))
        conteos[cat["clave"]] = n
        col.markdown(
            f"<div class='tui-kpi' style='border-top-color:{cat['color']}'>"
            f"<p class='valor' style='color:{cat['color']}'>{fmt(n)}</p>"
            f"<p class='etiqueta'>{cat['titulo']}</p></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='tui-seccion'></div>", unsafe_allow_html=True)

    for cat in categorias:
        sub = cat["filtro"](ambito).dropna(subset=[cat["orden"]])
        sub = sub.sort_values(cat["orden"], ascending=False)

        st.markdown(
            f"<div style='border-left:5px solid {cat['color']};background:#fff;"
            f"border:1px solid {BORDE};border-radius:6px;padding:12px 16px;margin:14px 0 8px'>"
            f"<div style='font-size:1.05rem;font-weight:700;color:{cat['color']}'>"
            f"{cat['titulo']} · {len(sub):,} municipios</div>".replace(",", ".")
            + f"<div style='color:{TEXTO_SUAVE};font-size:0.88rem;margin-top:4px'>"
              f"{cat['explica']}</div></div>",
            unsafe_allow_html=True,
        )

        if sub.empty:
            st.info("Ningún municipio cumple esta condición en el ámbito seleccionado.")
            continue

        debiles = int(sub["senal_debil"].sum())
        if debiles:
            st.caption(
                f"⚠️ {debiles} de estos {len(sub)} municipios tienen menos de "
                f"{UMBRAL_POBLACION_FIABLE} habitantes: su indicador puede estar inflado "
                f"por el efecto del denominador. Aparecen marcados en la columna "
                f"«Fiabilidad»."
            )

        tabla = pd.DataFrame({
            "Municipio": sub["nombre"],
            "Provincia": sub["provincia"],
            **{et: sub[c].round(2) for c, et in zip(cat["columnas"], cat["etiquetas"])},
            "Población": sub["poblacion"],
            "Fiabilidad": np.where(sub["senal_debil"], "⚠️ Señal débil", "Normal"),
            "Base del dato": sub["base_saturacion"],
            "Confianza": sub["cobertura_vut"].map(
                lambda c: CONFIANZA.get(c, ("—", ""))[0]),
            "Por qué salta": [cat["motivo"](f) for _, f in sub.iterrows()],
        })
        st.dataframe(tabla, hide_index=True, width="stretch",
                     height=min(420, 40 + 35 * len(tabla)))


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------

def escenarios(df: pd.DataFrame) -> None:
    st.markdown("### Simulador de escenarios")
    st.caption(
        "Recalcula la saturación de un municipio ante cambios en su oferta o su población, "
        "y sitúa el resultado frente al resto de España."
    )

    st.markdown(
        f"<div style='background:#fff8c5;border-left:5px solid #bf8700;"
        f"padding:11px 15px;border-radius:6px;margin:8px 0 16px;font-size:0.9rem'>"
        f"<b>Proyección orientativa, no una predicción.</b> El simulador aplica el cambio "
        f"directamente sobre el indicador —plazas ajustadas por cada 1.000 habitantes "
        f"ajustados— y no modela la demanda, la estacionalidad ni la respuesta del "
        f"mercado. Responde a «cuánto cambiaría el indicador si esto ocurriera», no a "
        f"«esto va a ocurrir».</div>",
        unsafe_allow_html=True,
    )

    # Sólo municipios con saturación calculada: sin ella no hay nada que proyectar.
    disponibles = df[df["saturacion_efectiva"].notna()
                     & (df["cobertura_vut"] != "no_comparable")].copy()
    if disponibles.empty:
        st.warning("No hay municipios con saturación calculada.")
        return

    izq, cen, der = st.columns([2, 1, 1])
    with izq:
        etiquetas = disponibles["nombre"] + " (" + disponibles["provincia"] + ")"
        opciones = dict(zip(etiquetas, disponibles["codigo_ine"]))
        eleccion = st.selectbox("Municipio", sorted(opciones), index=None,
                                placeholder="Escribe un nombre…", key="mun_escenario")
    with cen:
        delta_oferta = st.slider("Cambio en la oferta (%)", -50, 100, 0, step=5,
                                 key="delta_oferta",
                                 help="Variación de las plazas turísticas del municipio.")
    with der:
        delta_poblacion = st.slider("Cambio en la población (%)", -30, 50, 0, step=5,
                                    key="delta_poblacion",
                                    help="Variación de la población residente.")

    if not eleccion:
        st.info("Elige un municipio para simular un escenario.")
        return

    fila = disponibles[disponibles["codigo_ine"] == opciones[eleccion]].iloc[0]

    poblacion = float(fila["poblacion"])
    saturacion_actual = float(fila["saturacion_efectiva"])
    # Se reconstruyen las plazas desde el propio indicador, de modo que la base coincide
    # con la que se está mostrando: total (VUT + hotelera) donde existe, sólo VUT si no.
    plazas_actuales = saturacion_actual * poblacion / 1000

    plazas_sim = plazas_actuales * (1 + delta_oferta / 100)
    poblacion_sim = poblacion * (1 + delta_poblacion / 100)
    saturacion_sim = (1000 * plazas_sim / poblacion_sim) if poblacion_sim > 0 else float("nan")

    # Percentiles sobre el conjunto nacional con dato.
    serie = disponibles["saturacion_efectiva"].dropna()
    p_actual = 100 * float((serie < saturacion_actual).mean())
    p_sim = 100 * float((serie < saturacion_sim).mean())
    superados = int((serie < saturacion_sim).sum() - (serie < saturacion_actual).sum())

    cortes = escala_quintiles(serie)

    def tramo_de(valor: float) -> tuple[int, str]:
        etiquetas_tramo = ["Margen amplio", "Margen", "Intermedio",
                           "Presión notable", "Saturado"]
        for i in range(len(cortes) - 1):
            if valor <= cortes[i + 1]:
                return i + 1, etiquetas_tramo[min(i, len(etiquetas_tramo) - 1)]
        return len(cortes) - 1, etiquetas_tramo[-1]

    q_actual, nombre_actual = tramo_de(saturacion_actual)
    q_sim, nombre_sim = tramo_de(saturacion_sim)

    st.markdown(f"#### {fila['nombre']} · {fila['provincia']}")
    st.markdown(
        f"Base del dato: **{fila['base_saturacion']}** · "
        f"Confianza: {insignia_confianza(fila['cobertura_vut'])}",
        unsafe_allow_html=True,
    )
    if fila["ccaa"] in NOTAS_COBERTURA:
        st.caption(NOTAS_COBERTURA[fila["ccaa"]])

    # Un porcentaje sobre cero sigue siendo cero. En municipios sin oferta registrada el
    # simulador porcentual no puede decir nada, y conviene explicarlo antes de que el
    # usuario mueva el deslizador y no vea reacción alguna.
    if plazas_actuales <= 0:
        st.info(
            f"**{fila['nombre']} no tiene plazas turísticas registradas**, de modo que un "
            "cambio porcentual de la oferta deja el indicador en cero. Aquí la pregunta "
            "relevante no es cuánto crecer, sino qué supondría crear oferta desde cero; "
            "el deslizador de población sí produce efecto en cuanto exista alguna plaza.",
            icon="ℹ️",
        )

    colores_tramo = colores_por_tramo(VISTAS["Saturación"]["paleta"], len(cortes) - 1)
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(tarjeta_kpi(fmt(saturacion_actual, 1), "Saturación actual",
                            f"Q{q_actual}/5 · {nombre_actual} · percentil {p_actual:.0f}"),
                unsafe_allow_html=True)
    estilo_sim = "acento" if saturacion_sim > saturacion_actual else "neutro"
    k2.markdown(tarjeta_kpi(fmt(saturacion_sim, 1), "Saturación simulada",
                            f"Q{q_sim}/5 · {nombre_sim} · percentil {p_sim:.0f}",
                            estilo_sim), unsafe_allow_html=True)
    k3.markdown(tarjeta_kpi(fmt(plazas_actuales, 0) + " → " + fmt(plazas_sim, 0),
                            "Plazas turísticas",
                            f"{delta_oferta:+d} %"), unsafe_allow_html=True)
    k4.markdown(tarjeta_kpi(fmt(poblacion, 0) + " → " + fmt(poblacion_sim, 0),
                            "Población", f"{delta_poblacion:+d} %", "neutro"),
                unsafe_allow_html=True)

    # --- Barra comparativa antes / después ---
    tope = max(saturacion_actual, saturacion_sim, 1)
    def barra(valor: float, etiqueta: str, tramo: int) -> str:
        ancho = max(1.0, 100 * valor / tope)
        color = colores_tramo[min(tramo - 1, len(colores_tramo) - 1)]
        return (
            f"<div style='margin:6px 0'>"
            f"<div style='font-size:0.82rem;color:{TEXTO_SUAVE};margin-bottom:3px'>"
            f"{etiqueta}</div>"
            f"<div style='background:#E9EDF2;border-radius:4px;height:26px;position:relative'>"
            f"<div style='width:{ancho:.1f}%;background:{color};height:26px;"
            f"border-radius:4px'></div>"
            f"<div style='position:absolute;top:3px;left:10px;font-weight:700;"
            f"font-size:0.88rem;color:{TEXTO}'>{fmt(valor, 1)} plazas / 1.000 hab</div>"
            f"</div></div>"
        )

    st.markdown(
        f"<div style='background:#fff;border:1px solid {BORDE};border-radius:8px;"
        f"padding:14px 18px;margin-top:10px'>"
        + barra(saturacion_actual, "Situación actual", q_actual)
        + barra(saturacion_sim, "Escenario simulado", q_sim)
        + "</div>",
        unsafe_allow_html=True,
    )

    # --- Lectura del escenario ---
    if delta_oferta == 0 and delta_poblacion == 0:
        mensaje = ("Sin cambios aplicados. Mueve los deslizadores para simular una "
                   "variación de la oferta o de la población.")
        color = TUI_AZUL
    else:
        variacion = saturacion_sim - saturacion_actual
        sentido = "aumentaría" if variacion > 0 else "se reduciría"
        cambio_tramo = (
            f"pasaría de «{nombre_actual}» a «{nombre_sim}»" if q_sim != q_actual
            else f"se mantendría en «{nombre_actual}»"
        )
        comparacion = (
            f" y adelantaría a {abs(superados):,} municipios en el ranking nacional"
            .replace(",", ".") if superados > 0 else
            f" y quedaría por detrás de {abs(superados):,} municipios que hoy tiene por "
            f"debajo".replace(",", ".") if superados < 0 else ""
        )
        mensaje = (
            f"Con un cambio de <b>{delta_oferta:+d} %</b> en la oferta y "
            f"<b>{delta_poblacion:+d} %</b> en la población, la saturación de "
            f"<b>{fila['nombre']}</b> {sentido} de {fmt(saturacion_actual, 1)} a "
            f"<b>{fmt(saturacion_sim, 1)}</b> plazas por 1.000 habitantes. El municipio "
            f"{cambio_tramo}{comparacion}."
        )
        color = TUI_ROJO if q_sim > q_actual else (
            "#1a7f37" if q_sim < q_actual else TUI_AZUL)

    st.markdown(
        f"<div style='background:{color};color:#fff;padding:12px 16px;border-radius:6px;"
        f"margin-top:12px;font-size:0.95rem;line-height:1.55'>{mensaje}</div>",
        unsafe_allow_html=True,
    )


def ficha_municipio(df: pd.DataFrame) -> None:
    st.subheader("Ficha de municipio")

    etiquetas = df["nombre"] + " (" + df["provincia"] + ")"
    opciones = dict(zip(etiquetas, df["codigo_ine"]))
    eleccion = st.selectbox("Buscar municipio", sorted(opciones),
                            index=None, placeholder="Escribe un nombre…")
    if not eleccion:
        st.info("Elige un municipio para ver su ficha completa.")
        return

    fila = df[df["codigo_ine"] == opciones[eleccion]].iloc[0]

    st.markdown(f"### {fila['nombre']}  ·  {fila['provincia']}  ·  {fila['ccaa']}")
    st.markdown(f"Confianza del dato de VUT: {insignia_confianza(fila['cobertura_vut'])}",
                unsafe_allow_html=True)
    if fila["ccaa"] in NOTAS_COBERTURA:
        st.caption(NOTAS_COBERTURA[fila["ccaa"]])

    # Matices de la oportunidad: tipo y fiabilidad del denominador.
    if pd.notna(fila.get("indice_oportunidad")):
        tipo = fila.get("tipo_oportunidad")
        if tipo in TIPOS_OPORTUNIDAD:
            titulo_t, texto_t, color_t = TIPOS_OPORTUNIDAD[tipo]
            st.markdown(
                f"<div style='background:#fff;border:1px solid {BORDE};border-left:4px "
                f"solid {color_t};border-radius:6px;padding:10px 14px;margin:8px 0;"
                f"font-size:0.88rem'><b style='color:{color_t}'>{titulo_t}</b><br>"
                f"<span style='color:{TEXTO_SUAVE}'>{texto_t}</span></div>",
                unsafe_allow_html=True,
            )
    if fila.get("senal_debil"):
        st.warning(
            f"**Señal débil.** Con {fmt(fila['poblacion'])} habitantes, los indicadores "
            f"por habitante de este municipio se apoyan en un denominador muy pequeño: "
            f"basta un establecimiento para desplazarlo varios percentiles. Léelos como "
            f"orientación, no como medida estable.",
            icon="⚠️",
        )

    _lectura_automatica(fila, df)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Población", fmt(fila["poblacion"]))
    c2.metric("Superficie", fmt(fila["superficie_km2"], 1, " km²"))
    c3.metric("Saturación", fmt(fila["saturacion_efectiva"], 1),
              fila["base_saturacion"], delta_color="off")
    c4.metric("Al transporte", fmt(fila["dist_transporte_km"], 1, " km"), delta_color="off")

    st.markdown("#### Oferta registrada")
    oferta = pd.DataFrame([
        {"Capa": "VUT (registro oficial)", "Recuento": fila["n_vut_oficial"],
         "Plazas": fila["plazas_vut"], "Origen": fila["origen_vut"]},
        {"Capa": "Hotelera (EOH del INE)", "Recuento": fila["n_establecimientos_hoteleros"],
         "Plazas": fila["plazas_hoteleras"], "Origen": fila["origen_hotelero"]},
        {"Capa": "Alojamientos (OSM)", "Recuento": fila["n_alojamientos_osm"],
         "Plazas": None, "Origen": "openstreetmap"},
        {"Capa": "Camping", "Recuento": fila["n_camping"], "Plazas": None,
         "Origen": "openstreetmap"},
        {"Capa": "Restauración", "Recuento": fila["n_restauracion"], "Plazas": None,
         "Origen": "openstreetmap"},
        {"Capa": "Atracciones", "Recuento": fila["n_atracciones"], "Plazas": None,
         "Origen": "openstreetmap"},
        {"Capa": "Transporte", "Recuento": fila["n_transporte"], "Plazas": None,
         "Origen": "openstreetmap"},
    ])
    st.dataframe(oferta, hide_index=True, width="stretch")

    st.markdown("#### Posición frente al resto de España")
    st.caption("Percentil entre los municipios que tienen ese mismo indicador calculado.")
    indicadores = {
        "saturacion_efectiva": "Saturación por 1.000 hab",
        "densidad_plazas_km2": "Densidad de plazas por km²",
        "servicios_1000hab": "Servicios por 1.000 hab",
        "densidad_servicios_km2": "Densidad de servicios por km²",
        "indice_demanda": "Índice de demanda",
        "indice_riesgo": "Índice de riesgo",
        "indice_oportunidad": "Índice de oportunidad",
    }
    filas = []
    for columna, etiqueta in indicadores.items():
        valor = fila.get(columna)
        if pd.isna(valor):
            filas.append({"Indicador": etiqueta, "Valor": None, "Percentil": None,
                          "Comparado con": "sin dato"})
            continue
        base = df[columna].dropna()
        filas.append({
            "Indicador": etiqueta,
            "Valor": round(float(valor), 2),
            "Percentil": round(100 * (base < valor).mean(), 1),
            "Comparado con": f"{len(base):,} municipios".replace(",", "."),
        })
    st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# 4. Detalle geográfico
# ---------------------------------------------------------------------------

def detalle_geografico(df: pd.DataFrame) -> None:
    st.subheader("Detalle geográfico")
    st.caption(
        "Los puntos individuales de oferta. En las demás vistas manda el agregado "
        "municipal; aquí vive el dato de punto, para hacer zoom dentro de un municipio."
    )

    puntos = cargar_puntos()
    if puntos.empty:
        st.warning("No hay capas de punto disponibles.")
        return

    c1, c2 = st.columns([3, 1])
    capas = c1.multiselect("Capas", sorted(puntos["capa"].unique()),
                           default=["VUT registro oficial"], key="capas_detalle")
    limite = c2.number_input("Puntos máx. por capa", 500, 50_000, LIMITE_PUNTOS,
                             step=500, key="lim_detalle")

    etiquetas = df["nombre"] + " (" + df["provincia"] + ")"
    opciones = dict(zip(etiquetas, df["codigo_ine"]))
    centrar = st.selectbox("Centrar en un municipio", sorted(opciones),
                           index=None, placeholder="Opcional…", key="centro_detalle")

    if not capas:
        st.info("Selecciona al menos una capa.")
        return

    centro, zoom = [40.0, -3.7], 6
    if centrar:
        fila = df[df["codigo_ine"] == opciones[centrar]].iloc[0]
        if pd.notna(fila["lat_centro"]):
            centro, zoom = [fila["lat_centro"], fila["lon_centro"]], 13

    mapa = folium.Map(location=centro, zoom_start=zoom, tiles=TILES_BASE)
    muestreadas = []
    for capa in capas:
        sub = puntos[puntos["capa"] == capa]
        if len(sub) > limite:
            muestreadas.append((capa, len(sub), limite))
            sub = sub.sample(limite, random_state=SEMILLA)
        if sub.empty:
            continue
        grupo = folium.FeatureGroup(name=f"{capa} ({len(sub):,})".replace(",", "."))
        FastMarkerCluster(sub[["lat", "lon"]].values.tolist()).add_to(grupo)
        grupo.add_to(mapa)

    folium.LayerControl(collapsed=False).add_to(mapa)

    if muestreadas:
        detalle = " · ".join(f"{c}: {m:,} de {n:,}".replace(",", ".")
                             for c, n, m in muestreadas)
        st.info(f"**Muestra aleatoria**, no todos los puntos. {detalle}.", icon="🎲")

    st_folium(mapa, width=None, height=560, returned_objects=[])


# ---------------------------------------------------------------------------
# Panel de bienvenida
# ---------------------------------------------------------------------------

def panel_bienvenida() -> None:
    """
    Orientación para quien abre la herramienta por primera vez.

    El bloque de mayor peso visual es el de lectura del mapa. El motivo es que el
    significado del color cambia según el indicador y, sin decirlo de forma explícita, el
    visitante no puede saber si el verde es deseable o indeseable en lo que está viendo.
    """
    izq, cen, der = st.columns(3)
    with izq:
        st.markdown(
            "<div class='tui-tarjeta'><h4>Qué es</h4><p>Una herramienta de "
            "<b>inteligencia territorial turística</b>. Identifica, municipio a municipio, "
            "qué zonas de España están <b>saturadas</b> de oferta turística y cuáles "
            "conservan <b>margen de crecimiento</b>.</p></div>",
            unsafe_allow_html=True,
        )
    with cen:
        st.markdown(
            "<div class='tui-tarjeta'><h4>Para quién</h4><p>Para <b>gestores de destino</b>: "
            "administraciones locales y autonómicas, DMOs, consorcios turísticos e "
            "inversores. No está pensada para el viajero: no dice dónde alojarse, sino "
            "<b>dónde actuar</b>.</p></div>",
            unsafe_allow_html=True,
        )
    with der:
        st.markdown(
            f"<div class='tui-tarjeta' style='border-left:4px solid {TUI_ROJO}'>"
            "<h4>Por dónde empezar</h4><p>Para encontrar <b>dónde invertir</b>, usa el "
            "indicador <b>Oportunidad</b>.<br>Para encontrar <b>zonas en riesgo</b>, usa "
            "<b>Saturación</b>.</p></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='tui-seccion'></div>", unsafe_allow_html=True)
    # La explicación del color ya no vive aquí. Mostrar los tres indicadores a la vez
    # obligaba a averiguar cuál aplicaba y confundía, así que la leyenda se ha trasladado
    # junto al selector del mapa, donde se muestra únicamente la del indicador activo.
    st.markdown(
        f"<div style='background:#fff8c5;border-left:5px solid #bf8700;"
        f"padding:12px 16px;border-radius:6px;margin:10px 0 4px'>"
        f"<b>⬜ Gris significa SIN DATO, no saturación baja.</b> Esa comunidad autónoma no "
        f"publica un registro abierto de viviendas de uso turístico, así que no hay con qué "
        f"medirla. Confundir «sin dato» con «sin presión turística» convertiría a esos "
        f"municipios en falsas oportunidades de inversión."
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<div class='tui-seccion'></div>", unsafe_allow_html=True)
    st.markdown("#### Qué encontrarás en cada pestaña")
    guia = [
        ("🗺️", "Vista principal", "El mapa nacional coloreado por el indicador que elijas."),
        ("📊", "Rankings", "Los municipios más saturados y los de mayor oportunidad."),
        ("⚠️", "Alertas", "Municipios que cumplen condiciones de riesgo u oportunidad "
         "destacable."),
        ("🔮", "Escenarios", "Simula cambios en la oferta o la población y observa el "
         "efecto sobre la saturación."),
        ("📋", "Ficha de municipio", "Busca un municipio y consulta su diagnóstico y su "
         "recomendación."),
        ("📍", "Detalle geográfico", "Zoom hasta los establecimientos individuales."),
    ]
    for col, (icono, titulo, texto) in zip(st.columns(3), guia[:3]):
        col.markdown(
            f"<div class='tui-tarjeta'><h4>{icono} {titulo}</h4><p>{texto}</p></div>",
            unsafe_allow_html=True,
        )
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    for col, (icono, titulo, texto) in zip(st.columns(3), guia[3:]):
        col.markdown(
            f"<div class='tui-tarjeta'><h4>{icono} {titulo}</h4><p>{texto}</p></div>",
            unsafe_allow_html=True,
        )
    st.markdown("<div class='tui-seccion'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

aplicar_estilos()
cabecera()

if not INDICADORES.exists() or not GEOMETRIA.exists():
    st.error(
        "Faltan datos. Ejecuta en este orden:\n\n"
        "```\n"
        "python etl/extract/extract_ine_municipios.py\n"
        "python etl/transform/simplificar_geometria.py\n"
        "python etl/transform/agregar_municipal.py\n"
        "python etl/transform/calcular_indicadores.py\n"
        "```"
    )
    st.stop()

panel_bienvenida()

with st.spinner("Cargando indicadores…"):
    datos = cargar_indicadores()
    geometria = cargar_geometria()

con_registro = int((datos["cobertura_vut"] == "registro").sum())
con_hotel = int(datos["saturacion_total_1000hab"].notna().sum())
sin_dato = len(datos) - con_registro

k1, k2, k3, k4 = st.columns(4)
k1.markdown(tarjeta_kpi(fmt(len(datos)), "Municipios analizados",
                        "Base territorial del INE"), unsafe_allow_html=True)
k2.markdown(tarjeta_kpi(fmt(con_registro), "Con registro oficial de VUT",
                        f"{100 * con_registro / len(datos):.0f} % del total"),
            unsafe_allow_html=True)
k3.markdown(tarjeta_kpi(fmt(con_hotel), "Con dato hotelero (EOH)",
                        "Saturación total calculable", "acento"), unsafe_allow_html=True)
k4.markdown(tarjeta_kpi(fmt(sin_dato), "Sin registro publicado",
                        "No es saturación baja", "neutro"), unsafe_allow_html=True)

st.markdown("<div class='tui-seccion'></div>", unsafe_allow_html=True)

tabs = st.tabs(["🗺️ Vista principal", "📊 Rankings", "⚠️ Alertas", "🔮 Escenarios",
                "📋 Ficha de municipio", "📍 Detalle geográfico"])
with tabs[0]:
    vista_principal(datos, geometria)
with tabs[1]:
    rankings(datos)
with tabs[2]:
    alertas(datos)
with tabs[3]:
    escenarios(datos)
with tabs[4]:
    ficha_municipio(datos)
with tabs[5]:
    detalle_geografico(datos)
