"""
Visor preliminar del inventario de datos del TFM.

NO es el dashboard analítico. Es una herramienta de control para ver sobre el mapa qué se
ha extraído, con qué cobertura y dónde están los huecos. No calcula ningún indicador de
saturación ni de oportunidad: eso vendría después, y hacerlo ahora sobre una cobertura tan
desigual daría conclusiones falsas.

Lee directamente de data/processed/ y data/raw/. No necesita la base de datos. Si falta un
fichero o un proceso de extracción sigue corriendo, la capa correspondiente simplemente no
aparece y se avisa en pantalla.

Arranque:
    streamlit run dashboard/app_preliminar.py
"""

from __future__ import annotations

import json
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import FastMarkerCluster
from streamlit_folium import st_folium

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

LIMITE_PUNTOS = 5_000  # por capa; por encima se muestrea y se avisa
SEMILLA = 42

st.set_page_config(page_title="TFM · Inventario de datos", page_icon="🗺️", layout="wide")


# ---------------------------------------------------------------------------
# Definición de capas
# ---------------------------------------------------------------------------

CAPAS = {
    "alojamientos": {"etiqueta": "Alojamientos (OSM)", "color": "#1f77b4"},
    "vut": {"etiqueta": "VUT registro oficial", "color": "#d62728"},
    "camping": {"etiqueta": "Camping y autocaravanas", "color": "#2ca02c"},
    "restauracion": {"etiqueta": "Restauración (OSM)", "color": "#ff7f0e"},
    "atracciones": {"etiqueta": "Atracciones (OSM)", "color": "#9467bd"},
    "transporte_principales": {"etiqueta": "Transporte (OSM)", "color": "#17becf"},
}

COLOR_CAPA = {k: v["color"] for k, v in CAPAS.items()}

# Nivel de cobertura del registro oficial de VUT por territorio. Es lo que impide comparar
# comunidades entre sí y por eso se rotula de forma explícita en la interfaz.
COBERTURA_VUT = {
    "Andalucía": ("completa", "Registro autonómico completo (OpenRTA)."),
    "Canarias": ("completa", "Registro autonómico completo."),
    "Comunitat Valenciana": ("completa", "Registro autonómico completo."),
    "País Vasco": ("completa", "Registro autonómico (REATE), geocodificado con Nominatim."),
    "Galicia": (
        "municipal",
        "Sólo el 0,7 % del registro trae coordenadas. Los 28.465 registros son válidos a "
        "nivel de municipio, no de punto: NO se pintan en el mapa para no sugerir que "
        "Galicia tiene poca oferta.",
    ),
    "Illes Balears": ("parcial", "Sólo Mallorca. Menorca, Ibiza y Formentera dependen de sus consells."),
    "Cataluña": ("parcial", "Sólo la ciudad de Barcelona. El resto lo lleva la Generalitat."),
    "Comunidad de Madrid": (
        "distinta",
        "Sólo la ciudad, y mide LICENCIAS URBANÍSTICAS concedidas, no el registro "
        "turístico. Por eso son 997 y no decenas de miles: no es comparable con Barcelona.",
    ),
}

COLOR_COBERTURA = {
    "completa": "#1a7f37",
    "parcial": "#bf8700",
    "distinta": "#8250df",
    "municipal": "#0969da",
    "sin_registro": "#82071e",
}

# Sin registro oficial de VUT: en esos territorios sólo hay dato de OSM.
CCAA_SIN_REGISTRO = [
    "Aragón", "Cantabria", "Castilla-La Mancha", "Castilla y León", "Ceuta",
    "Extremadura", "La Rioja", "Melilla", "Navarra", "Principado de Asturias",
    "Región de Murcia",
]

# Fuentes de VUT: fichero normalizado y, si existe, su versión geocodificada (que es la
# que trae las coordenadas resueltas a posteriori).
FUENTES_VUT = {
    "andalucia": ("Andalucía", None),
    "canarias": ("Canarias", None),
    "baleares": ("Illes Balears", None),
    "barcelona": ("Cataluña", None),
    "madrid": ("Comunidad de Madrid", None),
    "pais_vasco": ("País Vasco", "vut_pais_vasco_geocodificado.csv"),
    "valencia": ("Comunitat Valenciana", "vut_valencia_geocodificado.csv"),
    "galicia": ("Galicia", None),
}


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

COLUMNAS_VUT = ["nombre", "lat", "lon", "ccaa", "provincia", "municipio"]


def _leer_csv(ruta: Path, columnas: list[str] | None = None) -> pd.DataFrame | None:
    """
    Lee un CSV tolerando que esté a medio escribir por un proceso en curso.

    `columnas` limita la lectura a lo que el visor necesita: los ficheros de VUT llegan a
    34 MB y leerlos enteros multiplica por varios minutos el arranque.
    """
    if not ruta.exists():
        return None
    try:
        if columnas:
            cabecera = pd.read_csv(ruta, nrows=0)
            columnas = [c for c in columnas if c in cabecera.columns]
        return pd.read_csv(ruta, low_memory=False, usecols=columnas or None)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, OSError, UnicodeDecodeError):
        return None


@st.cache_data(show_spinner=False)
def cargar_vut() -> tuple[pd.DataFrame, list[str]]:
    """Une los registros oficiales de VUT de todas las fuentes disponibles."""
    marcos, avisos = [], []

    for slug, (ccaa, fichero_geo) in FUENTES_VUT.items():
        df = None
        if fichero_geo:
            df = _leer_csv(PROCESSED_DIR / fichero_geo, COLUMNAS_VUT)
        if df is None:
            df = _leer_csv(PROCESSED_DIR / f"vut_normalizado_{slug}.csv", COLUMNAS_VUT)
        if df is None:
            avisos.append(f"VUT · {ccaa}: fichero no disponible todavía.")
            continue

        for c in COLUMNAS_VUT:
            if c not in df.columns:
                df[c] = pd.NA

        sub = df[COLUMNAS_VUT].copy()
        sub["ccaa"] = ccaa
        sub["capa"] = "vut"
        sub["tipo"] = "vivienda uso turístico"
        sub["fuente_slug"] = slug
        marcos.append(sub)

    if not marcos:
        return pd.DataFrame(), avisos
    return pd.concat(marcos, ignore_index=True), avisos


def _elementos_a_df(elementos: list[dict], ccaa: str, clave_tipo: tuple[str, ...]) -> pd.DataFrame:
    filas = []
    for el in elementos:
        tags = el.get("tags", {})
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            centro = el.get("center") or {}
            lat, lon = centro.get("lat"), centro.get("lon")

        tipo = "desconocido"
        for clave in clave_tipo:
            if clave in tags:
                tipo = tags[clave]
                break

        filas.append({
            "nombre": tags.get("name"),
            "lat": lat,
            "lon": lon,
            "ccaa": ccaa,
            "provincia": pd.NA,
            "municipio": tags.get("addr:city"),
            "tipo": tipo,
        })
    return pd.DataFrame(filas)


@st.cache_data(show_spinner=False)
def cargar_capa_osm(prefijo: str, claves_tipo: tuple[str, ...]) -> tuple[pd.DataFrame, list[str]]:
    """Carga una capa temática de OSM desde los JSON por CCAA de data/raw/."""
    avisos = []
    ficheros = sorted(RAW_DIR.glob(f"osm_{prefijo}_*.json"))
    ficheros = [f for f in ficheros if "consolidado" not in f.name]
    if not ficheros:
        return pd.DataFrame(), [f"Capa '{prefijo}': sin ficheros en data/raw/."]

    # Si una CCAA tiene varias fechas, se queda la más reciente.
    por_ccaa: dict[str, Path] = {}
    for f in ficheros:
        partes = f.stem.split("_")
        slug = partes[-2] if len(partes) >= 2 else f.stem
        if slug not in por_ccaa or f.name > por_ccaa[slug].name:
            por_ccaa[slug] = f

    marcos = []
    for slug, fichero in sorted(por_ccaa.items()):
        try:
            with fichero.open(encoding="utf-8") as fh:
                datos = json.load(fh)
        except (ValueError, OSError):
            avisos.append(f"Capa '{prefijo}' · {slug}: fichero ilegible o incompleto.")
            continue

        ccaa = datos.get("metadata", {}).get("ccaa_nombre", slug)
        elementos = datos.get("osm", {}).get("elements", [])
        if elementos:
            marcos.append(_elementos_a_df(elementos, ccaa, claves_tipo))

    if not marcos:
        return pd.DataFrame(), avisos

    df = pd.concat(marcos, ignore_index=True)
    df["capa"] = prefijo
    return df, avisos


@st.cache_data(show_spinner=False)
def cargar_camping() -> tuple[pd.DataFrame, list[str]]:
    """El camping ya está normalizado en data/processed/, con su propio esquema."""
    ficheros = sorted(PROCESSED_DIR.glob("camping_normalizado_*.csv"))
    if not ficheros:
        return pd.DataFrame(), ["Capa 'camping': aún no extraída."]

    marcos = []
    for f in ficheros:
        df = _leer_csv(f)
        if df is None or df.empty:
            continue
        for c in ("nombre", "lat", "lon", "ccaa", "provincia", "municipio", "tipo"):
            if c not in df.columns:
                df[c] = pd.NA
        marcos.append(df[["nombre", "lat", "lon", "ccaa", "provincia", "municipio", "tipo"]])

    if not marcos:
        return pd.DataFrame(), ["Capa 'camping': ficheros vacíos."]

    df = pd.concat(marcos, ignore_index=True)
    df["capa"] = "camping"
    return df, []


@st.cache_data(show_spinner=False)
def cargar_todo() -> tuple[dict[str, pd.DataFrame], list[str]]:
    capas: dict[str, pd.DataFrame] = {}
    avisos: list[str] = []

    vut, av = cargar_vut()
    avisos += av
    if not vut.empty:
        capas["vut"] = vut

    for prefijo, claves in [
        ("alojamientos", ("tourism",)),
        ("restauracion", ("amenity",)),
        ("atracciones", ("tourism", "historic")),
        ("transporte_principales", ("aeroway", "railway", "amenity", "public_transport")),
    ]:
        df, av = cargar_capa_osm(prefijo, claves)
        avisos += av
        if not df.empty:
            capas[prefijo] = df

    camping, av = cargar_camping()
    avisos += av
    if not camping.empty:
        capas["camping"] = camping

    return capas, avisos


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------

st.title("Inventario de datos · Dashboard de inteligencia territorial turística")
st.caption(
    "Visor de control de la extracción. **No calcula indicadores de saturación ni de "
    "oportunidad**: la cobertura es demasiado desigual entre territorios para que una "
    "comparación directa signifique algo."
)

with st.spinner("Cargando datos del inventario…"):
    capas, avisos = cargar_todo()

if not capas:
    st.error(
        "No se ha podido cargar ninguna capa. Ejecuta antes los scripts de extracción "
        "en `etl/extract/`."
    )
    st.stop()

for aviso in avisos:
    st.warning(aviso, icon="⚠️")

# --------------------------- Métricas de cobertura ---------------------------

st.subheader("Cobertura por capa")

columnas = st.columns(len(capas))
for col, (nombre_capa, df) in zip(columnas, capas.items()):
    total = len(df)
    con_coord = int((df["lat"].notna() & df["lon"].notna()).sum())
    pct = 100 * con_coord / total if total else 0
    with col:
        st.metric(
            CAPAS.get(nombre_capa, {}).get("etiqueta", nombre_capa),
            f"{total:,}".replace(",", "."),
            f"{pct:.1f} % con coordenadas",
            delta_color="off",
        )
        st.caption(f"{con_coord:,}".replace(",", ".") + " geolocalizados")

# --------------------------- Barra lateral ---------------------------

st.sidebar.header("Filtros")

capas_visibles = []
st.sidebar.markdown("**Capas**")
for nombre_capa in capas:
    etiqueta = CAPAS.get(nombre_capa, {}).get("etiqueta", nombre_capa)
    # Restauración se desactiva por defecto: son 112.000 puntos y domina el mapa.
    por_defecto = nombre_capa not in ("restauracion",)
    if st.sidebar.checkbox(etiqueta, value=por_defecto, key=f"capa_{nombre_capa}"):
        capas_visibles.append(nombre_capa)

todas_ccaa = sorted({c for df in capas.values() for c in df["ccaa"].dropna().unique()})
ccaa_sel = st.sidebar.multiselect(
    "Comunidades autónomas", todas_ccaa, default=[],
    help="Vacío = todas.",
)

tipos_disponibles = sorted({
    str(t) for nombre_capa in capas_visibles
    for t in capas[nombre_capa]["tipo"].dropna().unique()
})
tipos_sel = st.sidebar.multiselect(
    "Tipo de establecimiento", tipos_disponibles, default=[],
    help="Vacío = todos.",
)

limite = st.sidebar.slider(
    "Puntos máximos por capa", 500, 20_000, LIMITE_PUNTOS, step=500,
    help="Por encima de este número se muestra una muestra aleatoria.",
)

st.sidebar.divider()
st.sidebar.caption(
    "Este visor lee los ficheros de `data/`. No usa la base de datos de Railway."
)

# --------------------------- Preparación de puntos ---------------------------

def filtrar(df: pd.DataFrame) -> pd.DataFrame:
    sub = df[df["lat"].notna() & df["lon"].notna()].copy()
    if ccaa_sel:
        sub = sub[sub["ccaa"].isin(ccaa_sel)]
    if tipos_sel:
        sub = sub[sub["tipo"].astype(str).isin(tipos_sel)]
    return sub


# Galicia se excluye del mapa a propósito: sólo 212 de sus 28.465 VUT tienen coordenadas,
# y pintarlas sugeriría que Galicia apenas tiene oferta cuando lo que ocurre es que no
# está geolocalizada. Se informa aparte.
galicia_total = 0
if "vut" in capas:
    galicia_total = int((capas["vut"]["ccaa"] == "Galicia").sum())

muestreadas: list[tuple[str, int, int]] = []
puntos: dict[str, pd.DataFrame] = {}

for nombre_capa in capas_visibles:
    df = filtrar(capas[nombre_capa])
    if nombre_capa == "vut":
        df = df[df["ccaa"] != "Galicia"]
    n = len(df)
    if n > limite:
        muestreadas.append((nombre_capa, n, limite))
        df = df.sample(n=limite, random_state=SEMILLA)
    puntos[nombre_capa] = df

total_pintados = sum(len(d) for d in puntos.values())

# --------------------------- Avisos de cobertura ---------------------------

if muestreadas:
    detalle = " · ".join(
        f"{CAPAS.get(c, {}).get('etiqueta', c)}: {m:,} de {n:,}".replace(",", ".")
        for c, n, m in muestreadas
    )
    st.info(
        f"**Se está mostrando una muestra aleatoria**, no todos los puntos. {detalle}. "
        "Ajusta el límite en la barra lateral.",
        icon="🎲",
    )

if galicia_total and "vut" in capas_visibles:
    st.warning(
        f"**Galicia: cobertura municipal, no de punto.** Sus {galicia_total:,} VUT "
        "no se pintan en el mapa porque sólo el 0,7 % tiene coordenadas. Dibujar esos "
        "212 puntos daría la impresión falsa de que Galicia apenas tiene oferta.".replace(",", "."),
        icon="📍",
    )

# --------------------------- Mapa ---------------------------

st.subheader(f"Mapa · {total_pintados:,}".replace(",", ".") + " puntos representados")

mapa = folium.Map(location=[40.0, -3.7], zoom_start=6, tiles="cartodbpositron")


def callback_marcador(color: str) -> str:
    """
    Constructor de marcador en JavaScript para FastMarkerCluster.

    Construir un `folium.Marker` por punto en Python tarda minutos con decenas de miles de
    registros, porque cada uno renderiza su plantilla. FastMarkerCluster envía las
    coordenadas en bruto y crea los círculos en el navegador: el mismo mapa se arma en
    segundos.
    """
    return f"""
    function (row) {{
        var m = L.circleMarker(new L.LatLng(row[0], row[1]), {{
            radius: 5, color: "{color}", weight: 1,
            fillColor: "{color}", fillOpacity: 0.75
        }});
        m.bindPopup(row[2]);
        return m;
    }}
    """


for nombre_capa, df in puntos.items():
    if df.empty:
        continue
    conf = CAPAS.get(nombre_capa, {})
    etiqueta_capa = conf.get("etiqueta", nombre_capa)

    nombres = df["nombre"].fillna("(sin nombre)").astype(str).str.replace('"', "'", regex=False)
    popups = (
        "<b>" + nombres + "</b><br>" + etiqueta_capa
        + "<br>Tipo: " + df["tipo"].astype(str)
        + "<br>" + df["ccaa"].astype(str)
    )
    datos = [
        [float(la), float(lo), po]
        for la, lo, po in zip(df["lat"], df["lon"], popups)
    ]

    grupo = folium.FeatureGroup(
        name=f"{etiqueta_capa} ({len(df):,})".replace(",", ".")
    )
    FastMarkerCluster(
        data=datos,
        callback=callback_marcador(COLOR_CAPA.get(nombre_capa, "#666666")),
        options={"disableClusteringAtZoom": 15},
    ).add_to(grupo)
    grupo.add_to(mapa)

folium.LayerControl(collapsed=False).add_to(mapa)
st_folium(mapa, width=None, height=620, returned_objects=[])

# Leyenda de colores de las capas representadas.
if puntos:
    piezas = []
    for nombre_capa, df in puntos.items():
        if df.empty:
            continue
        conf = CAPAS.get(nombre_capa, {})
        piezas.append(
            f'<span style="display:inline-block;margin-right:18px;white-space:nowrap">'
            f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;'
            f'background:{conf.get("color", "#666")};margin-right:6px;'
            f'vertical-align:middle"></span>'
            f'{conf.get("etiqueta", nombre_capa)} '
            f'<b>{len(df):,}</b></span>'.replace(",", ".")
        )
    if piezas:
        st.markdown(
            '<div style="margin-top:-8px;font-size:0.9rem">' + "".join(piezas) + "</div>",
            unsafe_allow_html=True,
        )

# --------------------------- Leyenda de cobertura ---------------------------

st.subheader("Nivel de cobertura por territorio")
st.caption(
    "Los registros oficiales de VUT no son homogéneos. Esta tabla es la razón por la que "
    "este visor no compara comunidades: donde hay menos puntos puede haber menos oferta, "
    "o simplemente menos dato."
)

filas_cobertura = []
for ccaa, (nivel, nota) in COBERTURA_VUT.items():
    n = int((capas["vut"]["ccaa"] == ccaa).sum()) if "vut" in capas else 0
    filas_cobertura.append({
        "Comunidad": ccaa,
        "Cobertura VUT": nivel,
        "Registros": n,
        "Detalle": nota,
    })
for ccaa in CCAA_SIN_REGISTRO:
    filas_cobertura.append({
        "Comunidad": ccaa,
        "Cobertura VUT": "sin_registro",
        "Registros": 0,
        "Detalle": "Sin registro oficial incorporado: aquí sólo hay datos de OpenStreetMap.",
    })

tabla = pd.DataFrame(filas_cobertura).sort_values(
    ["Cobertura VUT", "Registros"], ascending=[True, False]
)


def colorear(fila: pd.Series) -> list[str]:
    color = COLOR_COBERTURA.get(fila["Cobertura VUT"], "#57606a")
    return [f"color: {color}; font-weight: 600" if c == "Cobertura VUT" else "" for c in fila.index]


st.dataframe(
    tabla.style.apply(colorear, axis=1),
    width="stretch",
    hide_index=True,
    column_config={"Detalle": st.column_config.TextColumn(width="large")},
)

with st.expander("Qué significa cada nivel de cobertura"):
    st.markdown(
        """
- **completa** — registro autonómico íntegro. Es el único caso en que el recuento se
  aproxima a la oferta declarada real.
- **parcial** — sólo una parte del territorio. Illes Balears cubre Mallorca pero no
  Menorca, Ibiza ni Formentera; Cataluña sólo la ciudad de Barcelona.
- **distinta** — Madrid mide **licencias urbanísticas concedidas**, no inscripciones en
  el registro turístico. Sus 997 registros no son comparables con los 10.627 de
  Barcelona: miden cosas diferentes.
- **municipal** — Galicia. Los 28.465 registros son fiables a nivel de municipio, pero
  sólo 212 tienen coordenadas, así que no se representan como puntos.
- **sin_registro** — no se ha incorporado registro oficial. Lo que se ve en el mapa en
  esos territorios procede sólo de OpenStreetMap, que infrarrepresenta el alojamiento no
  hotelero (en nuestra extracción, 59 % hotel frente a 11 % apartamento).

**Consecuencia práctica:** un mapa con menos puntos rojos en Aragón que en Andalucía no
significa que Aragón tenga menos viviendas turísticas. Significa que Andalucía publica su
registro y Aragón todavía no está incorporado.
        """
    )
