"""
Motor de recomendaciones por reglas para la ficha de municipio.

Clasifica la situación de un municipio a partir de los indicadores ya calculados y devuelve
un diagnóstico y una recomendación en lenguaje natural. La lógica es determinista y está
basada en percentiles: no interviene ningún modelo, de modo que toda recomendación puede
rastrearse hasta las reglas y los umbrales que la produjeron.

Principio que gobierna el módulo: **donde el dato no alcanza, no hay recomendación**. Un
municipio cuya comunidad no publica registro de vivienda de uso turístico aparecería, si se
tratase su ausencia de dato como un cero, con saturación nula y por tanto como oportunidad de
inversión. Ese es el error que el trabajo intenta evitar, así que la ausencia de dato produce
un mensaje explícito de insuficiencia y nunca una recomendación optimista.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Umbrales en percentil nacional, calculados sobre los municipios que tienen ese indicador.
P_SATURACION_ALTA = 90
P_SATURACION_MEDIA = 60
P_SERVICIOS_BUENOS = 55
P_ACCESO_BUENO = 60          # percentil de proximidad (invertido respecto a la distancia)
P_ATRACCIONES_RELEVANTES = 60

# Con menos de este número de habitantes, cualquier ratio por habitante se dispara por el
# denominador y no por la actividad turística. No impide la recomendación, pero la matiza.
POBLACION_MINIMA_FIABLE = 1_000


@dataclass
class Recomendacion:
    """Resultado del motor: categoría, texto y la evidencia que lo sustenta."""
    categoria: str
    titulo: str
    diagnostico: str
    recomendacion: str
    confianza: str                       # alta | media | insuficiente
    evidencias: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def color(self) -> str:
        return {
            "contencion": "#d73027",
            "crecimiento_controlado": "#fdae61",
            "oportunidad": "#1a9850",
            "potencial_limitado": "#4575b4",
            "sin_datos": "#6e7781",
        }.get(self.categoria, "#6e7781")


def _percentil(df: pd.DataFrame, columna: str, valor) -> float | None:
    """Posición del municipio en la distribución nacional de ese indicador, de 0 a 100."""
    if valor is None or pd.isna(valor) or columna not in df.columns:
        return None
    base = pd.to_numeric(df[columna], errors="coerce").dropna()
    if base.empty:
        return None
    return round(100 * float((base < valor).mean()), 1)


def _fmt(valor, decimales: int = 0) -> str:
    if valor is None or pd.isna(valor):
        return "sin dato"
    return f"{float(valor):,.{decimales}f}".replace(",", "·").replace(".", ",").replace("·", ".")


def evaluar(fila: pd.Series, df: pd.DataFrame) -> Recomendacion:
    """
    Clasifica un municipio y devuelve su recomendación.

    `fila` es el registro del municipio y `df` el conjunto nacional, necesario para situar
    cada indicador en su percentil.
    """
    avisos: list[str] = []

    # ------------------------------------------------------------------
    # 1. Comprobación de suficiencia de datos. Va primero por diseño.
    # ------------------------------------------------------------------
    origen_vut = fila.get("origen_vut")
    cobertura = fila.get("cobertura_vut")
    saturacion = fila.get("saturacion_efectiva")

    if origen_vut == "sin_dato" or pd.isna(saturacion):
        return Recomendacion(
            categoria="sin_datos",
            titulo="Datos insuficientes",
            diagnostico=(
                f"La comunidad de {fila.get('ccaa', '—')} no publica un registro de viviendas "
                "de uso turístico en formato reutilizable, por lo que no se dispone de una "
                "medida de la oferta reglada de este municipio. Lo único disponible procede de "
                "OpenStreetMap, que infrarrepresenta el alojamiento no hotelero de forma "
                "desigual entre territorios."
            ),
            recomendacion=(
                "**Datos insuficientes para una recomendación fiable en este territorio.** "
                "La ausencia de oferta registrada no debe interpretarse como ausencia de "
                "presión turística: significa que no hay registro publicado con el que medirla."
            ),
            confianza="insuficiente",
            evidencias=[
                f"Origen del dato de VUT: {origen_vut}",
                f"Alojamientos en OpenStreetMap: {_fmt(fila.get('n_alojamientos_osm'))}",
                f"Restauración y atracciones: {_fmt(fila.get('n_servicios'))} elementos",
            ],
        )

    if cobertura == "no_comparable":
        return Recomendacion(
            categoria="sin_datos",
            titulo="Dato no comparable",
            diagnostico=(
                "La fuente disponible para este municipio recoge licencias urbanísticas "
                "concedidas, no inscripciones en el registro turístico. Mide el acto "
                "administrativo de autorización y no la actividad declarada, de modo que sus "
                "cifras no son homologables a las del resto del territorio."
            ),
            recomendacion=(
                "**No procede una recomendación basada en saturación.** La magnitud disponible "
                "no es comparable con la de los demás municipios y su uso en un ranking "
                "produciría conclusiones erróneas."
            ),
            confianza="insuficiente",
            evidencias=[
                f"Licencias registradas: {_fmt(fila.get('n_vut_oficial'))}",
                "Fuente: Geoportal del Ayuntamiento de Madrid (licencias urbanísticas)",
            ],
        )

    # ------------------------------------------------------------------
    # 2. Percentiles de los indicadores que gobiernan la clasificación
    # ------------------------------------------------------------------
    p_saturacion = _percentil(df, "saturacion_efectiva", saturacion)
    p_servicios = _percentil(df, "servicios_1000hab", fila.get("servicios_1000hab"))
    p_atracciones = _percentil(df, "atracciones_1000hab", fila.get("atracciones_1000hab"))

    # La accesibilidad se expresa como distancia, así que su percentil se invierte para que
    # un valor alto signifique "bien conectado".
    p_dist = _percentil(df, "dist_transporte_km", fila.get("dist_transporte_km"))
    p_acceso = None if p_dist is None else round(100 - p_dist, 1)

    # ------------------------------------------------------------------
    # 3. Avisos que matizan la lectura sin impedirla
    # ------------------------------------------------------------------
    poblacion = fila.get("poblacion")
    if pd.notna(poblacion) and poblacion < POBLACION_MINIMA_FIABLE:
        avisos.append(
            f"Municipio de {_fmt(poblacion)} habitantes: los ratios por habitante son "
            "sensibles al tamaño del denominador y conviene leerlos con cautela."
        )

    base = fila.get("base_saturacion")
    if base == "sólo VUT":
        avisos.append(
            "La saturación se calcula sólo sobre vivienda de uso turístico: no se dispone de "
            "dato hotelero municipal, por lo que un destino de perfil hotelero aparecería "
            "infravalorado."
        )

    pct_geo = fila.get("pct_vut_geolocalizadas")
    if pd.notna(pct_geo) and pct_geo < 50:
        avisos.append(
            f"Sólo el {_fmt(pct_geo, 1)} % de las viviendas registradas está geolocalizada. "
            "El recuento municipal es fiable, pero el mapa de detalle está incompleto."
        )

    # ------------------------------------------------------------------
    # 4. Clasificación
    # ------------------------------------------------------------------
    evidencias = [
        f"Saturación: {_fmt(saturacion, 1)} plazas por 1.000 hab "
        f"(percentil {_fmt(p_saturacion, 0)} nacional, base: {base})",
        f"Servicios: {_fmt(fila.get('servicios_1000hab'), 1)} por 1.000 hab "
        f"(percentil {_fmt(p_servicios, 0)})",
        f"Atracciones: {_fmt(fila.get('n_atracciones'))} "
        f"(percentil {_fmt(p_atracciones, 0)} por habitante)",
        f"Transporte: {_fmt(fila.get('dist_transporte_km'), 1)} km al nodo más cercano "
        f"(percentil de accesibilidad {_fmt(p_acceso, 0)})",
    ]

    buena_accesibilidad = p_acceso is not None and p_acceso >= P_ACCESO_BUENO
    buenos_servicios = p_servicios is not None and p_servicios >= P_SERVICIOS_BUENOS
    hay_atracciones = p_atracciones is not None and p_atracciones >= P_ATRACCIONES_RELEVANTES

    if p_saturacion is not None and p_saturacion > P_SATURACION_ALTA:
        return Recomendacion(
            categoria="contencion",
            titulo="Zona de contención",
            diagnostico=(
                f"Con {_fmt(saturacion, 1)} plazas turísticas por cada 1.000 habitantes, el "
                f"municipio se sitúa en el percentil {_fmt(p_saturacion, 0)} nacional. La "
                "oferta reglada supera holgadamente lo que su población residente absorbe sin "
                "tensión."
            ),
            recomendacion=(
                "**Riesgo de sobrecarga turística.** No resulta recomendable incentivar nueva "
                "oferta de alojamiento. La prioridad debería situarse en la gestión de flujos, "
                "la sostenibilidad del destino y el seguimiento del impacto sobre el mercado "
                "residencial."
            ),
            confianza="alta" if base == "VUT + hotelera" else "media",
            evidencias=evidencias,
            avisos=avisos,
        )

    if (p_saturacion is not None and p_saturacion >= P_SATURACION_MEDIA
            and buena_accesibilidad and buenos_servicios):
        return Recomendacion(
            categoria="crecimiento_controlado",
            titulo="Zona consolidada con margen",
            diagnostico=(
                f"El municipio presenta una saturación apreciable pero no extrema (percentil "
                f"{_fmt(p_saturacion, 0)}), acompañada de una dotación de servicios y una "
                "conectividad por encima de la media. Es un destino ya activo que no ha "
                "alcanzado el umbral de sobrecarga."
            ),
            recomendacion=(
                "**Posible crecimiento controlado.** Existe margen para nueva oferta siempre "
                "que se dimensione y se acompañe de seguimiento de la presión sobre la "
                "vivienda residencial y los servicios públicos."
            ),
            confianza="alta" if base == "VUT + hotelera" else "media",
            evidencias=evidencias,
            avisos=avisos,
        )

    if p_saturacion is not None and p_saturacion < P_SATURACION_MEDIA:
        if buena_accesibilidad and (buenos_servicios or hay_atracciones):
            return Recomendacion(
                categoria="oportunidad",
                titulo="Oportunidad de desarrollo",
                diagnostico=(
                    f"La saturación se sitúa en el percentil {_fmt(p_saturacion, 0)}, por "
                    "debajo de la media, mientras que la accesibilidad y la dotación de "
                    "servicios o recursos se encuentran por encima. El municipio reúne "
                    "condiciones de demanda que su oferta reglada actual no aprovecha."
                ),
                recomendacion=(
                    "**Demanda potencial infrautilizada.** El territorio admite desarrollo de "
                    "oferta de alojamiento. Conviene verificar que la baja saturación no "
                    "responde a limitaciones normativas o de suelo antes de promover inversión."
                ),
                confianza="alta" if base == "VUT + hotelera" else "media",
                evidencias=evidencias,
                avisos=avisos,
            )

        if not buena_accesibilidad:
            return Recomendacion(
                categoria="potencial_limitado",
                titulo="Potencial limitado por conectividad",
                diagnostico=(
                    f"La oferta reglada es baja (percentil {_fmt(p_saturacion, 0)}), pero el "
                    f"nodo de transporte más cercano se encuentra a "
                    f"{_fmt(fila.get('dist_transporte_km'), 1)} km, lo que sitúa al municipio "
                    "entre los peor conectados."
                ),
                recomendacion=(
                    "**Potencial limitado.** La escasa oferta refleja también una baja "
                    "conectividad. Cualquier estrategia de desarrollo turístico debería "
                    "abordar antes la accesibilidad que la promoción o la oferta de plazas."
                ),
                confianza="media",
                evidencias=evidencias,
                avisos=avisos,
            )

    # Situación intermedia que no encaja en ninguna regla anterior.
    return Recomendacion(
        categoria="crecimiento_controlado",
        titulo="Situación intermedia",
        diagnostico=(
            f"El municipio se sitúa en el percentil {_fmt(p_saturacion, 0)} de saturación, sin "
            "que su dotación de servicios ni su accesibilidad destaquen de forma suficiente "
            "para clasificarlo en ninguna de las categorías definidas."
        ),
        recomendacion=(
            "**Sin señal clara.** Los indicadores no dibujan ni una situación de sobrecarga ni "
            "una oportunidad definida. Se recomienda un análisis cualitativo del destino antes "
            "de tomar decisiones de inversión o contención."
        ),
        confianza="media",
        evidencias=evidencias,
        avisos=avisos,
    )
