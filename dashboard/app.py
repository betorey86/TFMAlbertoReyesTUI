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
        "etiqueta": "Índice de oportunidad (demanda − saturación)",
        "paleta": ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850"],
        "ayuda": "Verde = demanda alta con saturación baja. Rojo = ya saturado respecto a "
                 "su demanda.",
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

def vista_principal(df: pd.DataFrame, geo: dict) -> None:
    st.subheader("Mapa de saturación y oportunidad")

    izq, der = st.columns([2, 3])
    with izq:
        vista = st.radio("Indicador", list(VISTAS), horizontal=True, key="vista_mapa")
    conf = VISTAS[vista]
    columna = conf["columna"]

    with der:
        ccaa_sel = st.multiselect(
            "Filtrar comunidades", sorted(df["ccaa"].dropna().unique()),
            default=[], key="ccaa_mapa", help="Vacío = toda España.",
        )

    datos = df[df["ccaa"].isin(ccaa_sel)] if ccaa_sel else df
    con_dato = datos[datos[columna].notna()]

    st.caption(conf["ayuda"])

    # --- Métricas de cobertura, siempre visibles ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Municipios", fmt(len(datos)))
    c2.metric("Con dato", fmt(len(con_dato)),
              f"{100 * len(con_dato) / len(datos):.0f} %" if len(datos) else "—",
              delta_color="off")
    c3.metric("Sin dato", fmt(len(datos) - len(con_dato)), delta_color="off")
    if vista == "Saturación":
        fiables = int((datos["base_saturacion"] == "VUT + hotelera").sum())
        c4.metric("Con dato hotelero", fmt(fiables), delta_color="off")
    else:
        c4.metric("Mediana", fmt(con_dato[columna].median(), 1) if len(con_dato) else "—",
                  delta_color="off")

    if con_dato.empty:
        st.warning("No hay datos para esta selección.")
        return

    # Escala por cuantiles: estos indicadores tienen colas muy largas (hay municipios con
    # 1.500 plazas por 1.000 habitantes y la mediana ronda cero). Una escala lineal dejaría
    # el 99 % del mapa del mismo color.
    cortes = list(con_dato[columna].quantile([0, .5, .75, .9, .97, .995, 1]).unique())
    if len(cortes) < 3:
        cortes = [con_dato[columna].min(), con_dato[columna].max()]
    escala = cm.LinearColormap(conf["paleta"], vmin=min(cortes), vmax=max(cortes)) \
        .to_step(index=cortes)
    escala.caption = conf["etiqueta"]

    valores = dict(zip(con_dato["codigo_ine"], con_dato[columna]))
    visibles = set(datos["codigo_ine"])

    mapa = folium.Map(location=[40.0, -3.7], zoom_start=6, tiles="cartodbpositron")

    def estilo(elemento):
        codigo = elemento["properties"].get("codigo_ine")
        if codigo not in visibles:
            return {"fillOpacity": 0, "weight": 0}
        valor = valores.get(codigo)
        # El "sin dato" tiene su propio color y jamás entra en la escala verde-rojo.
        color = COLOR_SIN_DATO if valor is None or pd.isna(valor) else escala(valor)
        return {"fillColor": color, "color": "#ffffff", "weight": 0.25, "fillOpacity": 0.8}

    # Cifras del popup, adjuntadas a la geometría.
    campos = ["nombre", "provincia", "poblacion", "plazas_vut", "plazas_hoteleras",
              "saturacion_efectiva", "base_saturacion", "indice_oportunidad",
              "dist_transporte_km", "cobertura_vut"]
    tabla = datos.set_index("codigo_ine")[campos].to_dict("index")
    for elemento in geo["features"]:
        codigo = elemento["properties"].get("codigo_ine")
        info = tabla.get(codigo)
        p = elemento["properties"]
        if info is None:
            p["_nombre"] = p.get("nombre_municipio", "—")
            p["_resumen"] = "Fuera de la selección"
            continue
        p["_nombre"] = f"{info['nombre']} ({info['provincia']})"
        p["_poblacion"] = fmt(info["poblacion"]) + " hab"
        p["_vut"] = fmt(info["plazas_vut"]) + " plazas"
        p["_hotel"] = fmt(info["plazas_hoteleras"]) + " plazas"
        p["_saturacion"] = (fmt(info["saturacion_efectiva"], 1) + " /1.000 hab"
                            if pd.notna(info["saturacion_efectiva"]) else "sin dato")
        p["_base"] = info["base_saturacion"]
        p["_confianza"] = CONFIANZA.get(info["cobertura_vut"], ("—", ""))[0]

    folium.GeoJson(
        geo,
        style_function=estilo,
        highlight_function=lambda _: {"weight": 2, "color": "#111111"},
        tooltip=folium.GeoJsonTooltip(
            fields=["_nombre", "_poblacion", "_vut", "_hotel", "_saturacion",
                    "_base", "_confianza"],
            aliases=["", "Población:", "Plazas VUT:", "Plazas hoteleras:",
                     "Saturación:", "Base del cálculo:", "Confianza del dato:"],
            sticky=True,
            labels=True,
        ),
        smooth_factor=1.0,
    ).add_to(mapa)

    escala.add_to(mapa)
    st_folium(mapa, width=None, height=560, returned_objects=[])

    # --- Leyenda del "sin dato" ---
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:8px;margin-top:-8px'>"
        f"<span style='display:inline-block;width:18px;height:18px;"
        f"background:{COLOR_SIN_DATO};border:1px solid #999'></span>"
        f"<b>Sin dato</b> — no es saturación baja. Son municipios cuya comunidad no publica "
        f"registro de VUT, o donde el dato disponible mide otra magnitud."
        f"</div>",
        unsafe_allow_html=True,
    )

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

    mapa = folium.Map(location=centro, zoom_start=zoom, tiles="cartodbpositron")
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
# Aplicación
# ---------------------------------------------------------------------------

st.title("Inteligencia territorial turística de España")
st.caption(
    "Herramienta de apoyo a la gestión de destinos. Identifica zonas saturadas y zonas con "
    "margen, a nivel municipal."
)

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

with st.spinner("Cargando indicadores…"):
    datos = cargar_indicadores()
    geometria = cargar_geometria()

con_registro = int((datos["cobertura_vut"] == "registro").sum())
con_hotel = int(datos["saturacion_total_1000hab"].notna().sum())
st.info(
    f"**{len(datos):,} municipios**. ".replace(",", ".")
    + f"{con_registro:,} con registro oficial de VUT ".replace(",", ".")
    + f"({100 * con_registro / len(datos):.0f} %), de los cuales {con_hotel:,} "
      .replace(",", ".")
    + "cuentan además con dato hotelero municipal de la EOH. "
      "El resto aparece como **sin dato**, que no es lo mismo que saturación baja.",
    icon="ℹ️",
)

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
