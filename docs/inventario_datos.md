# Inventario de datos

Estado de todas las capas y fuentes del proyecto. Es la base del capítulo de metodología y
la referencia para la fase de fusión.

**Generado automáticamente** por `etl/transform/generar_inventario.py` leyendo los ficheros
reales de `data/raw/` y `data/processed/`. Las cifras no están escritas a mano: se
recalculan en cada ejecución. Última generación: 13/08/2026 12:16 UTC.

## Resumen

| | Registros |
|---|---:|
| VUT de registros oficiales | 393.148 |
| — con resolución de punto | 364.683 |
| — geolocalizados | 300.772 (82.5 %) |
| Elementos de OpenStreetMap | 171.373 |
| **Total** | **564.521** |

## Inventario detallado

| Capa | Territorio | Fuente | Registros | Con coord. | % coord. | Resolución | Confianza | Limitaciones declaradas |
|---|---|---|---:|---:|---:|---|---|---|
| VUT oficial | Andalucía | OpenRTA — Junta de Andalucía | 168.796 | 159.880 | 94.7 % | punto | alta | Volcado de 325 MB con todas las tipologías; el filtrado a VUT se hace en local. Formato decimal mixto en las coordenadas. |
| VUT oficial | Canarias | Registro General Turístico de Canarias | 72.645 | 48.606 | 66.9 % | punto | alta | 23.979 filas traen (0,0) como ausencia de coordenada, no como posición: se anulan. |
| VUT oficial | Comunitat Valenciana | Generalitat Valenciana — dadesobertes.gva.es | 89.978 | 67.700 | 75.2 % | punto | alta | Sin coordenadas en origen; geocodificado por referencia catastral. Quedan parcelas pendientes por bloqueo temporal del Catastro. |
| VUT oficial | Galicia | REAT — Xunta de Galicia | 28.465 | — | — | municipal | parcial | Sólo el 0,7 % trae coordenadas y ningún geocodificador supera el 45 %. Se explota a nivel de concello. El fichero de origen incluía datos personales (nombre y DNI del titular), excluidos en la lectura. |
| VUT oficial | Illes Balears (sólo Mallorca) | Consell de Mallorca | 16.854 | 8.909 | 52.9 % | punto | parcial | Sólo Mallorca. Menorca, Ibiza y Formentera dependen de sus propios consells y publican por separado. |
| VUT oficial | Cataluña (sólo ciudad de Barcelona) | Open Data BCN | 10.627 | 10.627 | 100.0 % | punto | parcial | Sólo el municipio de Barcelona. El registro del resto de Cataluña lo lleva la Generalitat y no publica volcado equivalente. |
| VUT oficial | País Vasco | Open Data Euskadi — REATE | 4.786 | 4.053 | 84.7 % | punto | alta | Sin coordenadas en origen; geocodificado con Nominatim. El 14,5 % no resuelto son barrios rurales dispersos. |
| VUT oficial | Comunidad de Madrid (sólo ciudad) | Geoportal Ayuntamiento de Madrid | 997 | 997 | 100.0 % | punto | no comparable | Mide LICENCIAS URBANÍSTICAS concedidas, no inscripciones en el registro turístico. No es la misma magnitud que el resto. |
| Alojamientos OSM | España (19 CCAA) | OpenStreetMap (Overpass) | 25.271 | 25.271 | 100.0 % | punto | solo OSM | OSM infrarrepresenta el alojamiento no hotelero y el sesgo no es uniforme entre CCAA: no comparable entre territorios sin contrastar. |
| Restauración | España (19 CCAA) | OpenStreetMap (Overpass) | 112.235 | 112.235 | 100.0 % | punto | solo OSM | El bar de barrio y el restaurante turístico comparten etiqueta: mide densidad de hostelería, no especialización turística. |
| Atracciones | España (19 CCAA) | OpenStreetMap (Overpass) | 28.694 | 28.694 | 100.0 % | punto | solo OSM | Cobertura desigual; `viewpoint` domina el recuento y no equivale a recurso turístico gestionado. |
| Transporte | España (19 CCAA) | OpenStreetMap (Overpass) | 4.322 | 4.322 | 100.0 % | punto | solo OSM | Perfil de nodos de entrada (estaciones, aeropuertos, ferris). No incluye paradas urbanas. |
| Camping | España (parcial: 5/19 CCAA) | OpenStreetMap (Overpass) | 851 | 851 | 100.0 % | punto | solo OSM | `capacity` presente en una fracción mínima de los registros y `camp_site` mezcla camping comercial con acampada libre: sirve para contar establecimientos, no capacidad. |


### Cómo leer la columna "Confianza"

- **alta** — registro oficial íntegro del territorio, con la magnitud que dice medir.
- **parcial** — cubre sólo una parte del territorio, o su geolocalización es incompleta.
- **no comparable** — mide una magnitud distinta a la del resto de fuentes de su capa.
- **solo OSM** — cartografía colaborativa, sin respaldo de registro administrativo.

## Territorios sin registro oficial de VUT

En estas 11 comunidades **no hay dato de registro administrativo**
incorporado: lo único disponible es OpenStreetMap.

- Aragón
- Cantabria
- Castilla-La Mancha
- Castilla y León
- Ceuta
- Extremadura
- La Rioja
- Melilla
- Navarra
- Principado de Asturias
- Región de Murcia

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
autonómico. Por eso son 997
registros frente a los 10.627
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

- **306 concellos** con al menos una VUT registrada.
- **156.026 plazas declaradas**, con una cobertura del 98.8 % de los registros.
- El municipio está en el 100 % de los 28.465 registros.

Agregación en `data/processed/vut_galicia_municipal.csv`, con la clave `clave_join` preparada para cruzar con la población municipal del INE y calcular el ratio de plazas por habitante.

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

| Tipo | Elementos | % |
|---|---:|---:|
| hotel | 14.853 | 58.8 % |
| guest_house | 3.877 | 15.3 % |
| hostel | 3.703 | 14.7 % |
| apartment | 2.838 | 11.2 % |

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
