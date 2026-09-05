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
            padding: 22px 28px;
            margin-bottom: 22px;
            display: flex; align-items: center; gap: 20px;
        }}
        .tui-cabecera h1 {{
            color: #fff; font-size: 1.85rem; font-weight: 700;
            margin: 0; letter-spacing: -0.3px; line-height: 1.2;
        }}
        .tui-cabecera p {{
            color: #B9C7D8; margin: 6px 0 0; font-size: 0.95rem;
        }}
        /* Hueco reservado para el logotipo corporativo. Sustituir el bloque .tui-logo
           por <img src="..."> cuando se disponga del archivo. */
        .tui-logo {{
            width: 62px; height: 62px; border-radius: 8px; flex-shrink: 0;
            background: rgba(255,255,255,0.12);
            border: 1px dashed rgba(255,255,255,0.35);
            display: flex; align-items: center; justify-content: center;
            color: rgba(255,255,255,0.55); font-size: 0.62rem; text-align: center;
            font-weight: 600; letter-spacing: 0.5px;
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
          <!-- Hueco para el logotipo corporativo: sustituir por <img src="assets/logo.svg"> -->
          <div class="tui-logo">LOGO</div>
          <div>
            <h1>Inteligencia Territorial Turística de España</h1>
            <p>Saturación y oportunidad de inversión, municipio a municipio ·
               Herramienta de apoyo a la gestión de destinos</p>
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


def tabla_vista(seleccion: pd.DataFrame, vista: str, conf: dict, columna: str) -> None:
    """
    Lista de los municipios que el mapa está mostrando, ordenada por el indicador activo.

    Comparte el marco de datos con el mapa, de modo que ambos responden a los mismos
    filtros sin posibilidad de desincronizarse.
    """
    if seleccion.empty:
        st.info("Ningún municipio cumple los filtros seleccionados.")
        return

    # La columna de confianza dice de dónde sale el dato, que en este proyecto es tan
    # relevante como el propio valor.
    if vista == "Saturación":
        confianza = seleccion["base_saturacion"]
    else:
        confianza = seleccion["cobertura_vut"].map(
            lambda c: CONFIANZA.get(c, ("—", ""))[0]
        )

    tabla = pd.DataFrame({
        "Municipio": seleccion["nombre"],
        "Provincia": seleccion["provincia"],
        conf["etiqueta"]: seleccion[columna].round(2),
        "Población": seleccion["poblacion"],
        "Base del dato": confianza,
    }).sort_values(conf["etiqueta"], ascending=False)

    st.dataframe(
        tabla, hide_index=True, height=520, width="stretch",
        column_config={
            "Población": st.column_config.NumberColumn(format="%d"),
            conf["etiqueta"]: st.column_config.NumberColumn(format="%.2f"),
        },
    )


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

    valores = dict(zip(con_dato["codigo_ine"], con_dato[columna]))
    # Con un filtro de nivel activo el mapa muestra sólo los municipios del tramo; sin él,
    # todo el ámbito, incluidos los que carecen de dato, que se pintan en gris.
    visibles = set(seleccion["codigo_ine"]) if nivel else set(datos["codigo_ine"])

    mapa = folium.Map(location=[40.0, -3.7], zoom_start=6, tiles=TILES_BASE)

    def estilo(elemento):
        codigo = elemento["properties"].get("codigo_ine")
        if codigo not in visibles:
            return {"fillOpacity": 0, "weight": 0}
        valor = valores.get(codigo)
        # El "sin dato" tiene su propio color y jamás entra en la escala verde-rojo.
        color = COLOR_SIN_DATO if valor is None or pd.isna(valor) else escala(valor)
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
    tabla = datos.set_index("codigo_ine")[campos].to_dict("index")

    for elemento in geo["features"]:
        codigo = elemento["properties"].get("codigo_ine")
        info = tabla.get(codigo)
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
        st_folium(mapa, width=None, height=560, returned_objects=[])

    with col_tabla:
        st.markdown(
            f"<div style='font-weight:700;color:{TUI_AZUL};font-size:1rem;"
            f"margin-bottom:2px'>Municipios mostrados</div>"
            f"<div style='color:{TEXTO_SUAVE};font-size:0.83rem;margin-bottom:8px'>"
            f"{len(seleccion):,} municipios · ordenados por {conf['etiqueta'].lower()}"
            f"</div>".replace(",", "."),
            unsafe_allow_html=True,
        )
        tabla_vista(seleccion, vista, conf, columna)

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
        ("📋", "Ficha de municipio", "Busca un municipio y consulta su diagnóstico y su "
         "recomendación."),
        ("📍", "Detalle geográfico", "Zoom hasta los establecimientos individuales."),
    ]
    for col, (icono, titulo, texto) in zip(st.columns(4), guia):
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

tabs = st.tabs(["🗺️ Vista principal", "📊 Rankings", "📋 Ficha de municipio",
                "📍 Detalle geográfico"])
with tabs[0]:
    vista_principal(datos, geometria)
with tabs[1]:
    rankings(datos)
with tabs[2]:
    ficha_municipio(datos)
with tabs[3]:
    detalle_geografico(datos)
