# 5. Resultados

## 5.1. El sistema construido

El resultado material del trabajo es un sistema de inteligencia territorial turística
compuesto por una cadena ETL reproducible, una base de datos de agregados municipales y un
cuadro de mando analítico orientado al gestor de destino.

La base de datos resultante integra 567.080 registros procedentes de tres familias de fuentes:
393.148 viviendas de uso turístico de ocho registros oficiales, 173.932 elementos de
OpenStreetMap distribuidos en cinco capas temáticas, y la Encuesta de Ocupación Hotelera del
INE en sus desagregaciones provincial y por punto turístico. Todo ello se agrega sobre una base
territorial de 8.132 municipios que suman 49.114.494 habitantes y 504.979 kilómetros cuadrados.

La cobertura efectiva de los indicadores es desigual por construcción y se declara de forma
explícita. De los 8.132 municipios, 2.034 disponen de registro oficial de viviendas turísticas
y 1.972 tienen agregado calculado; 132 son punto turístico de la EOH y disponen por tanto de
dato hotelero municipal; y 74 reúnen ambas condiciones, que es el conjunto sobre el que puede
calcularse el indicador de saturación turística total.

[PENDIENTE: captura del dashboard, vista principal]
[PENDIENTE: captura del dashboard, ficha de municipio]

## 5.2. La saturación medida sobre vivienda de uso turístico

El indicador de saturación restringido a vivienda turística, expresado en plazas por cada mil
habitantes y calculado sobre municipios de más de mil habitantes, ordena el territorio del modo
siguiente.

| Municipio | Provincia | Población | Plazas VUT | Plazas / 1.000 hab |
|---|---|---:|---:|---:|
| Peñíscola | Castellón | 8.774 | 13.484 | 1.536,81 |
| Benahavís | Málaga | 9.472 | 14.222 | 1.501,48 |
| Mojácar | Almería | 7.680 | 9.964 | 1.297,40 |
| Alcalà de Xivert | Castellón | 7.349 | 9.468 | 1.288,34 |
| Oropesa del Mar | Castellón | 12.640 | 13.953 | 1.103,88 |
| Teulada | Alicante | 12.912 | 12.754 | 987,76 |
| Sanxenxo | Pontevedra | 18.016 | 17.538 | 973,47 |
| Barreiros | Lugo | 3.036 | 2.934 | 966,40 |

La lectura de estas cifras exige detenerse en su magnitud. Un valor de 1.536,81 plazas por mil
habitantes significa que Peñíscola dispone de **más de una plaza y media de vivienda turística
por cada residente empadronado**. Benahavís, en la Costa del Sol, presenta 1,5 plazas por
habitante. Estos no son valores marginales de una distribución continua: la mediana nacional del
indicador se sitúa próxima a cero, de modo que la cola derecha describe un fenómeno
cualitativamente distinto y no una gradación.

Resulta significativa la composición geográfica del ranking. Las ocho primeras posiciones
corresponden a municipios costeros de tres ámbitos: el litoral mediterráneo peninsular
(Castellón y Alicante), la Costa del Sol y el litoral andaluz oriental, y las Rías Baixas y la
Mariña lucense. La presencia de Sanxenxo y Barreiros confirma además la validez de la ruta de
agregación municipal directa diseñada para Galicia, ya que ambos municipios se incorporan al
ranking sin disponer de geolocalización de sus viviendas.

## 5.3. El efecto de incorporar la capa hotelera

La hipótesis que motivó la incorporación de la Encuesta de Ocupación Hotelera era que un
indicador construido exclusivamente sobre vivienda turística infravalora sistemáticamente los
destinos de turismo hotelero. Los datos la confirman con claridad.

| Municipio | Sólo VUT | VUT + hotelera | % hotelero | Puesto sólo VUT | Puesto total |
|---|---:|---:|---:|---:|---:|
| Calvià | 64,04 | 1.102,97 | 94,2 % | 51 | 21 |
| Sant Llorenç des Cardassar | 244 | 2.878,99 | 91,5 % | 34 | 1 |
| Muro | 235 | 2.188,26 | 89,3 % | 37 | 4 |
| Alcúdia | 379,06 | 1.443,42 | 73,7 % | 23 | 11 |
| Benidorm | 255,07 | 877,48 | 70,9 % | 32 | 23 |

El caso de **Calvià** es el más elocuente. El municipio mallorquín que integra Magaluf y
Palmanova, uno de los destinos de sol y playa de mayor intensidad de España, ocupaba la posición
51 del ranking de saturación con 64 plazas por mil habitantes. Incorporando sus 55.887 plazas
hoteleras, el indicador asciende a 1.102,97 y el 94,2 % de su capacidad resulta ser hotelera. Un
sistema construido exclusivamente sobre vivienda turística no habría detectado en absoluto la
presión turística de Calvià.

Benidorm ilustra el mismo fenómeno con una composición más equilibrada: 19.724 plazas de vivienda
turística frente a 48.129 hoteleras, lo que eleva su indicador de 255,07 a 877,48.

Encabeza el ranking de saturación total **Sant Llorenç des Cardassar**, con 2.878,99 plazas por
mil habitantes, seguido de Peñíscola con 2.685,43 y Mojácar con 2.573,83. Estos tres municipios
disponen, por tanto, de entre dos y media y tres plazas turísticas por residente.

Conviene señalar que dos destinos hoteleros de primer orden —Salou y Lloret de Mar— quedan fuera
de este análisis pese a disponer de dato hotelero, porque Cataluña sólo publica registro de
vivienda turística para la ciudad de Barcelona. El sistema los presenta como ausencia de dato y
no como saturación baja, conforme al criterio expuesto en el capítulo 4.

## 5.4. El índice de riesgo

El índice de riesgo, que combina saturación y presión de servicios, identifica los siguientes
municipios como los de mayor exposición.

| Municipio | Provincia | Población | Índice de riesgo |
|---|---|---:|---:|
| Alcalà de Xivert | Castellón | 7.349 | 93,8 |
| Mojácar | Almería | 7.680 | 93,6 |
| Calp | Alicante | 27.616 | 93,0 |
| Peñíscola | Castellón | 8.774 | 92,2 |
| Fisterra | A Coruña | 4.666 | 92,0 |
| Xàbia/Jávea | Alicante | 30.642 | 91,9 |
| Valldemossa | Illes Balears | 2.038 | 91,6 |
| Santiago del Teide | S. C. de Tenerife | 12.582 | 91,6 |
| Corcubión | A Coruña | 1.659 | 91,5 |
| Valle Gran Rey | S. C. de Tenerife | 4.830 | 91,3 |

La distribución territorial resultante es coherente con el conocimiento del sector: litoral
mediterráneo, archipiélago balear, sur de Tenerife y La Gomera, y la Costa da Morte gallega. La
presencia de municipios de pequeño tamaño demográfico —Valldemossa con 2.038 habitantes,
Corcubión con 1.659— apunta a un rasgo característico del fenómeno: la mayor presión relativa no
se concentra necesariamente en los grandes destinos, sino en municipios pequeños con oferta
desproporcionada respecto a su población residente.

## 5.5. Accesibilidad

La distancia mediana de los municipios españoles al nodo de transporte más próximo —aeropuerto,
terminal marítima, estación ferroviaria o de autobuses— es de 8,36 kilómetros. Los municipios
peor conectados de más de mil habitantes son Molina de Aragón (Guadalajara), con 35,40
kilómetros; Alcántara (Cáceres), con 35,22; Morella (Castellón), con 35,18; Cervantes (Lugo), con
34,80; y Fermoselle (Zamora), con 34,17.

Es relevante que tres de estos cinco municipios —Morella, Alcántara y Fermoselle— poseen conjuntos
históricos de valor patrimonial reconocido. La coincidencia entre recurso turístico y déficit de
accesibilidad delimita un perfil de destino cuya activación depende más de la conectividad que de
la promoción.

La capa de transporte incorpora 4.322 nodos, entre los que figuran 2.056 estaciones ferroviarias,
770 estaciones de autobuses, 364 aeródromos y 247 terminales marítimas. La incorporación explícita
de aeropuertos y terminales de ferry resultó necesaria: en los territorios insulares constituyen
la puerta de entrada principal al destino, muy por encima del ferrocarril.

## 5.6. El índice de oportunidad y su interpretación

El índice de oportunidad, definido como la diferencia entre demanda potencial y saturación,
identifica los siguientes municipios.

| Municipio | Provincia | Población | Índice de oportunidad |
|---|---|---:|---:|
| Deba | Gipuzkoa | 5.362 | 74,0 |
| Pasaia | Gipuzkoa | 15.820 | 68,7 |
| Artziniega | Araba/Álava | 1.868 | 66,4 |
| Iruña Oka | Araba/Álava | 3.704 | 65,3 |
| Ormaiztegi | Gipuzkoa | 1.293 | 63,9 |
| Ademuz | Valencia | 1.015 | 62,3 |
| Bergara | Gipuzkoa | 14.404 | 61,7 |
| Laza | Ourense | 1.156 | 61,7 |

Este resultado requiere una lectura prudente y se presenta con reservas explícitas. El
predominio de municipios vascos es internamente consistente con los datos —el País Vasco registra
4.786 viviendas de uso turístico para 2,2 millones de habitantes, una densidad muy inferior a la
mediterránea— pero admite dos interpretaciones que los datos disponibles no permiten discriminar.

La primera es sustantiva: el País Vasco presenta efectivamente una baja penetración de la vivienda
de uso turístico y dispone de margen de crecimiento. La segunda es metodológica: el REATE podría
aplicar criterios de inscripción distintos a los registros mediterráneos, de modo que la
comparación estaría midiendo en parte una diferencia normativa y no una diferencia de mercado.
Discriminar entre ambas hipótesis exige un análisis comparado de las normativas autonómicas de
inscripción que queda fuera del alcance de este trabajo.

Se documenta además una limitación estructural del índice: la saturación que lo alimenta no
incorpora capacidad hotelera salvo en los 74 municipios con dato de la EOH, por lo que los
destinos de perfil hotelero situados fuera de ese conjunto aparecen infravalorados en su
saturación y, en consecuencia, sobrevalorados en su oportunidad.

## 5.7. Síntesis de resultados

Los resultados permiten sostener tres afirmaciones.

En primer lugar, la presión turística medida sobre oferta reglada presenta en España una
distribución extraordinariamente asimétrica: mientras la mediana municipal se aproxima a cero,
un conjunto reducido de municipios costeros supera holgadamente la plaza turística por habitante,
alcanzando en los casos extremos ratios cercanos a tres plazas por residente.

En segundo lugar, la composición de esa oferta varía de forma decisiva entre destinos, de modo
que cualquier indicador construido sobre una sola tipología de alojamiento produce diagnósticos
erróneos. El caso de Calvià, que pasa de la posición 51 a la 21 al incorporar la capacidad
hotelera, cuantifica ese efecto.

En tercer lugar, y como resultado de naturaleza distinta, el trabajo evidencia que la principal
limitación para un sistema de inteligencia territorial turística en España no es de orden técnico
sino de disponibilidad de datos: once comunidades y ciudades autónomas no publican su registro de
vivienda turística en formato reutilizable, y 6.097 de los 8.132 municipios quedan por ello fuera
de todo cálculo de saturación.

[PENDIENTE: mapa coroplético de saturación para incorporar como figura]
[PENDIENTE: gráfico de distribución del indicador de saturación]
