# 3. Metodología

## 3.1. Planteamiento general: una arquitectura de capas desiguales

El diseño metodológico de este trabajo parte de una constatación empírica que condiciona todo
lo demás: en España no existe una fuente única, homogénea y de cobertura nacional que describa
la oferta turística a escala municipal. Existen, en cambio, dos familias de fuentes con
propiedades complementarias y defectos opuestos.

La primera es la cartografía colaborativa, representada por OpenStreetMap (OSM). Su virtud es
la cobertura: proporciona geometría para la totalidad del territorio nacional, con un mismo
esquema de etiquetado y sin discontinuidades administrativas. Su defecto es la
representatividad, ya que refleja lo que la comunidad de mapeadores ha cartografiado, no lo
que existe.

La segunda familia la constituyen los registros administrativos oficiales de alojamiento
turístico. Su virtud es la validez, dado que un registro autonómico de viviendas de uso
turístico (en adelante, VUT) es el censo administrativo de la actividad declarada. Su defecto
es la fragmentación: cada comunidad autónoma regula, publica y estructura su registro con
criterios propios, y once de las diecinueve comunidades y ciudades autónomas no publican
ninguno en formato reutilizable.

De esta asimetría surge el principio metodológico central del trabajo, que denominamos
**arquitectura de capas desiguales**. En lugar de forzar una homogeneidad artificial —bien
descartando las fuentes oficiales por no ser universales, bien tratando la cartografía
colaborativa como si fuese un censo—, el sistema integra ambas familias conservando de forma
explícita la trazabilidad de qué fuente sustenta cada dato en cada territorio. OSM opera como
capa base de cobertura nacional; los registros oficiales operan como capa de precisión allí
donde existen; y una tercera capa estadística, la Encuesta de Ocupación Hotelera (EOH) del
Instituto Nacional de Estadística (INE), aporta la dimensión hotelera. La consecuencia
operativa es que el sistema no produce un único número por municipio, sino un número
acompañado de la declaración de su procedencia y de su nivel de confianza.

## 3.2. Fuentes de datos

Las fuentes efectivamente incorporadas, con los volúmenes verificados que documenta el
inventario reproducible del proyecto (`docs/inventario_datos.md`, generado automáticamente por
lectura de los ficheros de datos), son las siguientes.

**Registros oficiales de viviendas de uso turístico.** Se han incorporado ocho fuentes que
suman 393.148 registros. Andalucía aporta 168.796 registros a través de la API pública OpenRTA
de la Junta de Andalucía; la Comunitat Valenciana, 89.978 desde el portal
`dadesobertes.gva.es`; Canarias, 72.645 desde el Registro General Turístico; Galicia, 28.465
desde el Registro de Empresas e Actividades Turísticas (REAT) de la Xunta; el Consell de
Mallorca, 16.854 correspondientes exclusivamente a esa isla; el Ayuntamiento de Barcelona,
10.627 de su municipio; Open Data Euskadi, 4.786 procedentes del REATE; y el Geoportal del
Ayuntamiento de Madrid, 997 correspondientes a licencias urbanísticas concedidas en la ciudad.
Esta última fuente, como se razona en el capítulo 4, mide una magnitud distinta a las demás y
recibe un tratamiento diferenciado.

**Cartografía colaborativa.** Mediante la API Overpass de OpenStreetMap se han extraído
173.932 elementos organizados en cinco capas temáticas, cubriendo las diecinueve comunidades y
ciudades autónomas: 112.235 establecimientos de restauración, 28.694 atracciones y recursos
patrimoniales, 25.271 alojamientos, 3.410 campings y áreas de autocaravana, y 4.322 nodos de
transporte.

**Fuente estadística hotelera.** La EOH del INE (operación IOE 30235) se ha incorporado a
través de su API Tempus3, empleando la tabla 2066 para el desglose provincial —de cobertura
nacional— y la tabla 2076 para los 132 puntos turísticos que el organismo monitoriza de forma
individualizada, que constituyen el único nivel de desagregación municipal disponible.

**Base territorial.** La unidad de análisis procede del seccionado censal del INE
(`seccionado_2026`), del que se han derivado 8.132 municipios, y del padrón municipal continuo,
que aporta una población total de 49.114.494 habitantes referida a 2025.

## 3.3. La unidad de análisis: el municipio

La elección del municipio como unidad de análisis se justifica por tres razones, frente a las
dos alternativas consideradas.

La **sección censal** ofrece mayor resolución y habría permitido un análisis intraurbano más
fino. Se descartó porque la mayoría de las fuentes oficiales de alojamiento declaran el
municipio pero no la sección, y porque el denominador de los indicadores —la población— es
publicado por el INE con garantías a escala municipal. Trabajar con secciones habría exigido
imputar tanto el numerador como el denominador, introduciendo incertidumbre en ambos lados del
cociente.

La **retícula regular** presenta la ventaja de la homogeneidad geométrica y habría neutralizado
el efecto de la desigual extensión de los términos municipales. Se descartó porque el
destinatario del sistema es el gestor de destino, cuya capacidad de intervención —ordenanzas,
licencias, tasas, planificación— se ejerce sobre unidades administrativas y no sobre celdas. Un
indicador expresado en una unidad sobre la que nadie tiene competencia es un indicador que no
puede accionarse.

El **municipio**, finalmente, es la unidad en la que coinciden la competencia administrativa, la
disponibilidad del denominador poblacional y la declaración del alojamiento en los registros
oficiales. Es además la única resolución en la que Galicia puede integrarse en condiciones de
igualdad, por las razones que se exponen en el capítulo 4.

La clave de cruce entre todas las fuentes es sistemáticamente el **código INE de cinco
dígitos**, nunca el topónimo. Esta decisión, que puede parecer un detalle de implementación,
resultó determinante: el INE denomina «Coruña, A» a lo que los registros autonómicos escriben
«A CORUÑA», y «Balears, Illes» a lo que el registro balear escribe «Illes Balears». Los cruces
basados en cadenas de texto fallan precisamente en los municipios de mayor tamaño, que son los
que más pesan en cualquier agregado.

## 3.4. El proceso ETL

El sistema se organiza como una cadena de extracción, transformación y carga implementada en
Python, con los datos intermedios persistidos en ficheros para permitir la reejecución parcial
de cualquier etapa.

### 3.4.1. Extracción

La extracción de OpenStreetMap se realiza mediante consultas Overpass QL delimitadas por el
código ISO 3166-2 de cada comunidad autónoma, criterio más estable que la búsqueda por topónimo
ante nombres bilingües. Las consultas se resuelven sobre nodos, vías y relaciones, empleando la
directiva `out center` para obtener el centroide de las geometrías poligonales; sin ella, los
9.610 elementos cartografiados como vía y los 397 como relación quedarían sin coordenadas.

El proceso incorpora tres mecanismos de robustez que la experiencia de ejecución demostró
necesarios. El primero es la rotación entre réplicas del servicio Overpass, dado que la
instancia principal devuelve errores de saturación con frecuencia. El segundo es un reintento
con espera creciente. El tercero, y el más relevante metodológicamente, es la detección de
**respuestas degradadas**: durante la primera ejecución por lotes, la comunidad de La Rioja
devolvió cero elementos con código de respuesta HTTP 200, lo que el sistema habría almacenado
como una extracción válida. La misma consulta repetida minutos después devolvió 195 elementos.
Dado que ninguna comunidad autónoma española carece de alojamientos, un resultado vacío se
trata desde entonces como error reintentable, y sólo se acepta como dato cuando se reproduce de
forma consistente en todas las réplicas y reintentos.

La extracción de los registros oficiales se realiza mediante una función independiente por
organismo, lo que permite incorporar nuevas comunidades sin alterar las existentes. Cada fuente
presenta particularidades de formato que se documentan en el código: OpenRTA publica un volcado
de 325 MB con todas las tipologías turísticas y mezcla punto y coma decimal en la misma columna
de coordenadas; el registro canario codifica la ausencia de coordenada como el par (0,0), lo que
afecta a 23.979 filas; el fichero gallego concatena dos bloques con esquemas distintos.

### 3.4.2. Transformación y normalización

Todas las fuentes se normalizan a un esquema común de oferta que comprende identificador de
origen, denominación, tipo, coordenadas, adscripción territorial, capacidad en plazas y fecha de
extracción. La normalización preserva el valor original de la fuente en un campo de subtipo, de
modo que la clasificación agregada no destruye la información de partida.

### 3.4.3. Geocodificación

Dos de los registros oficiales incorporados no publican coordenadas, y un tercero las publica de
forma incompleta, lo que obligó a diseñar una estrategia de geocodificación diferenciada por
fuente.

Para el **País Vasco**, cuyo censo de 4.786 viviendas sólo publica dirección postal, se empleó
el servicio Nominatim de OpenStreetMap respetando su política de uso de una petición por
segundo. La tasa de resolución inicial fue del 47 %, insuficiente. El análisis de los fallos
reveló patrones sistemáticos en la escritura de las direcciones: el número de portal aparece
unido a la mano (`6IZ`), la puerta figura como componente suelto, los tipos de vía se abrevian y
los genéricos se escriben en las dos lenguas cooficiales separados por barra
(`Zumardia/Alameda Mazarredo`). Corregida la normalización e introduciendo una cascada de
consultas alternativas, la tasa ascendió al **85,5 %**, con 4.053 registros geolocalizados con
municipio coincidente. El 14,5 % no resuelto corresponde mayoritariamente a barrios rurales
dispersos que la cartografía colaborativa no recoge con ese topónimo.

Para la **Comunitat Valenciana**, cuyo registro publica referencia catastral en el 99,1 % de sus
89.978 registros, se empleó el servicio de cartografía catastral, concretamente el método
`Consulta_CPMRC` del servicio OVCCoordenadas. La referencia registral consta de veinte
posiciones mientras que el servicio exige las catorce de parcela; el truncamiento necesario
tiene el efecto colateral favorable de reducir 89.201 consultas a 35.070 parcelas únicas, puesto
que numerosas viviendas comparten edificio. La tasa de acierto efectiva sobre las parcelas
consultadas fue del 99,8 %, con una coincidencia de municipio del 99,97 % verificada contra el
domicilio que devuelve el propio servicio.

Para **Galicia** se ensayaron ambos servicios sobre una misma muestra aleatoria de 300
direcciones, con idéntica semilla y limpieza previa para permitir la comparación pareada,
obteniéndose un 45,0 % con el Catastro y un 26,7 % con Cartociudad del Instituto Geográfico
Nacional. La decisión resultante —renunciar a la geocodificación y trabajar a resolución
municipal— se razona en el capítulo 4.

### 3.4.4. Agregación municipal

La agregación admite dos rutas de entrada hacia una misma tabla de resultados. La primera es el
**cruce espacial**, aplicable a las capas de OpenStreetMap, que no declaran municipio: cada punto
se asigna al polígono municipal que lo contiene. Sobre un total de 474.704 puntos, el 99,77 %
quedó contenido en un municipio. Los 1.077 huérfanos resultaron ser, en su totalidad, elementos
situados dentro del territorio nacional —ninguno con coordenadas invertidas o fuera de España— y
mayoritariamente costeros o portuarios, con una distancia mediana al municipio más próximo de 26
metros. Se estableció una regla de reasignación al municipio más cercano cuando la distancia es
inferior a 500 metros, que recupera 1.014 elementos con una mediana de 20,3 metros,
descartándose los 63 restantes, cuya distancia mediana asciende a 1.233 metros.

La segunda ruta es la **agregación por municipio declarado**, aplicable a los registros
oficiales. Esta decisión corrige un error de diseño inicial que se documenta como caso de
validación en el capítulo 4: contar las viviendas por su posición geográfica hace que el recuento
municipal dependa de que la geocodificación esté completa, cuando el municipio consta en el 100 %
de los registros oficiales. La geolocalización sigue siendo necesaria para el mapa de detalle,
pero no para comparar territorios.

Cada bloque de contadores se acompaña de una columna `origen_agregacion` que toma tres valores:
`join_espacial`, `municipal_directo` y `sin_dato`. Su función se detalla en el capítulo 4.

## 3.5. Cálculo de indicadores

Los indicadores se calculan sobre la tabla de agregados municipales y se normalizan de dos formas
complementarias: por habitante, que mide la presión sobre la comunidad residente, y por
superficie, que mide la concentración territorial.

El **indicador de saturación** se expresa como plazas turísticas por cada mil habitantes. Se
calcula en dos variantes: una restringida a las plazas de VUT, de cobertura más amplia, y otra
que suma las plazas hoteleras de la EOH, disponible únicamente en los municipios que son punto
turístico. La segunda es metodológicamente preferible pero sólo puede calcularse en 74
municipios, por lo que ambas coexisten y el sistema declara siempre cuál está empleando.

En cuanto a la dimensión temporal de la EOH, que es una operación mensual, se ha optado por el
**mes de máxima capacidad de los últimos doce** en lugar de la media anual. La razón es de
homogeneidad: las plazas de VUT son capacidad registrada, permanentemente disponible sobre el
papel, mientras que las plazas de la EOH son capacidad efectivamente abierta en el mes de
referencia. Promediar el año penalizaría a los destinos estacionales de costa, que son
precisamente aquellos cuya presión turística se pretende medir.

El **indicador de accesibilidad** mide la distancia al nodo de transporte más próximo
—aeropuerto, terminal marítima, estación ferroviaria o de autobuses— calculada sobre la esfera
mediante la fórmula del haversine. Se descartó el uso de una proyección plana porque el
territorio analizado incluye Canarias, situada a más de 1.700 kilómetros de la Península, donde
cualquier huso UTM peninsular deformaría las distancias hasta alterar el orden de los vecinos más
próximos.

Los **índices compuestos** se construyen sobre rangos percentiles y no sobre valores normalizados
linealmente. La razón es la forma de las distribuciones: el municipio más saturado alcanza 1.537
plazas por mil habitantes mientras que la mediana se sitúa próxima a cero, de modo que una
normalización lineal comprimiría al 99 % de los municipios contra el extremo inferior de la
escala.

El **índice de demanda** pondera los servicios por habitante (40 %), las atracciones por habitante
(25 %), la densidad de servicios por kilómetro cuadrado (20 %) y la accesibilidad (15 %). El peso
principal recae deliberadamente sobre las magnitudes per cápita: la densidad por superficie mide
urbanidad y no especialización turística, como se documenta en el capítulo 4.

El **índice de oportunidad** se define como la diferencia entre demanda y saturación, y el
**índice de riesgo** como la combinación ponderada de saturación (65 %) y demanda (35 %). Ambos
dependen de conocer la saturación, por lo que no se calculan en los territorios sin registro de
VUT ni en aquellos cuyo dato mide una magnitud distinta.

[PENDIENTE: diagrama de la arquitectura del pipeline]
[PENDIENTE: cita bibliográfica sobre indicadores de capacidad de carga turística]
[PENDIENTE: justificación bibliográfica de las ponderaciones de los índices compuestos]
