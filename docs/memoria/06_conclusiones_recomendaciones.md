# 6. Conclusiones y recomendaciones

> **Estado del capítulo:** pendiente de redacción. Requiere aportación del autor. Este documento
> recoge los materiales del proyecto sobre los que puede construirse.

## 6.1. Conclusiones sobre los objetivos planteados

[PENDIENTE: redacción, en correspondencia con los objetivos que fije el capítulo 1]

## 6.2. Conclusiones metodológicas

Material disponible para su desarrollo:

La arquitectura de capas desiguales se ha mostrado viable y, sobre todo, necesaria: ninguna
fuente individual permite cubrir el territorio nacional con validez suficiente. La condición
para que funcione es que la trazabilidad de la procedencia acompañe al dato en todo el
recorrido, desde la extracción hasta la visualización.

La distinción entre cero real y ausencia de dato, materializada en la columna
`origen_agregacion`, resultó ser la decisión de diseño de mayor impacto sobre la validez de los
resultados. Sin ella, los 6.097 municipios sin registro publicado habrían aparecido como
territorios sin presión turística.

El caso de validación documentado en el apartado 4.4 sugiere una conclusión sobre el propio
proceso de construcción de indicadores: los tres errores encadenados que llevaron al sistema a
recomendar inversión en Peñíscola no produjeron ningún fallo visible. La detección dependió del
contraste con el conocimiento experto del dominio, lo que apunta a la conveniencia de incorporar
esa validación como etapa formal del método y no como revisión final.

## 6.3. Recomendaciones para la gestión de destinos

[PENDIENTE: redacción]

Ejes posibles, derivados de los resultados:

- La concentración de la presión turística en municipios pequeños con oferta desproporcionada
  respecto a su población sugiere que los instrumentos de contención deben dimensionarse a esa
  escala y no a la del gran destino.
- La coincidencia entre recurso patrimonial y déficit de accesibilidad en municipios del
  interior delimita un perfil de destino cuya activación depende de la conectividad.
- La composición de la oferta —hotelera frente a vivienda turística— varía tanto entre destinos
  que condiciona qué instrumento de política es aplicable en cada caso.

## 6.4. Recomendaciones sobre publicación de datos

[PENDIENTE: redacción]

Este apartado puede sostenerse íntegramente sobre hallazgos del trabajo:

- Once comunidades y ciudades autónomas no publican registro reutilizable de vivienda de uso
  turístico, lo que impide cualquier análisis comparado de cobertura nacional.
- La heterogeneidad de magnitudes entre fuentes —el caso de Madrid, que publica licencias
  urbanísticas y no inscripciones registrales— exige que los organismos declaren con precisión
  qué mide cada conjunto de datos.
- La publicación de coordenadas en los registros mejoraría sustancialmente la explotabilidad:
  Galicia obliga a trabajar a resolución municipal por no publicarlas.
- Se documenta un caso de publicación de datos personales de titulares particulares en un
  conjunto de datos abierto, lo que sugiere la conveniencia de revisar los procedimientos de
  anonimización previos a la publicación.

## 6.5. Líneas de trabajo futuro

[PENDIENTE: redacción]

Elementos identificados durante el desarrollo:

- Incorporación de los registros hoteleros autonómicos como capa de precisión municipal, que
  ampliaría el indicador de saturación total más allá de los 74 municipios actuales.
- Completar la geocodificación pendiente de la Comunitat Valenciana.
- Incorporación de indicadores de demanda efectiva (pernoctaciones, estancia media) frente a los
  indicadores de oferta que sustentan el trabajo actual.
- Incorporación de rutas de senderismo y otros recursos de geometría lineal, descartados en esta
  fase por no encajar en un modelo de puntos.
- Análisis comparado de las normativas autonómicas de inscripción, necesario para discriminar si
  las diferencias territoriales observadas responden al mercado o al criterio registral.
