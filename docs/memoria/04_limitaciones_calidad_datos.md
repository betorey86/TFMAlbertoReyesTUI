# 4. Limitaciones y calidad de los datos

Este capítulo no presenta un listado de deficiencias del trabajo, sino el conjunto de
decisiones metodológicas que la calidad real de las fuentes obligó a tomar. Se ha optado por
documentarlas con detalle, incluidos los errores detectados durante el desarrollo y su
corrección, porque en un sistema de apoyo a la decisión territorial la trazabilidad de esas
decisiones forma parte del resultado: un indicador cuyo procedimiento de construcción no es
auditable no puede sustentar una política pública.

## 4.1. El sesgo de OpenStreetMap y la necesidad de los registros oficiales

El punto de partida del proyecto contemplaba OpenStreetMap como fuente principal de oferta de
alojamiento. La extracción completa para las diecinueve comunidades y ciudades autónomas
—25.271 elementos— permitió contrastar esa hipótesis con datos y refutarla.

El reparto por tipología resultó ser el siguiente: 14.853 hoteles (59 %), 3.877 casas de
huéspedes (15 %), 3.703 albergues (15 %) y 2.838 apartamentos (11 %). En un país donde la
vivienda de uso turístico ocupa el centro del debate sobre saturación, esa proporción no
describe el mercado, sino qué se cartografía en OpenStreetMap. El caso balear resulta
ilustrativo: 1.763 hoteles frente a 186 apartamentos, cuando el registro del Consell de
Mallorca contabiliza 16.854 viviendas turísticas sólo en esa isla.

La conclusión metodológica es doble. Por un lado, OpenStreetMap conserva plena utilidad como
capa geográfica base y como fuente de servicios, atracciones y transporte, ámbitos en los que
no compite con ningún registro administrativo. Por otro, el denominador de oferta de
alojamiento sobre el que se calcula cualquier indicador de saturación debe proceder de los
registros oficiales. Es importante señalar que **el sesgo no es uniforme entre comunidades**,
lo que impide además comparar territorios entre sí utilizando exclusivamente la cartografía
colaborativa.

## 4.2. Madrid: licencias urbanísticas frente a registro turístico

El fichero publicado por el Geoportal del Ayuntamiento de Madrid contiene 997 registros de
viviendas de uso turístico. La cifra contrasta con los 10.627 de la ciudad de Barcelona, y la
tentación de leer esa diferencia como una menor presión turística en Madrid sería un error de
interpretación grave.

La fuente madrileña no recoge inscripciones en el registro turístico autonómico, sino
**licencias urbanísticas concedidas** para el uso de vivienda turística. Mide, por tanto, el
acto administrativo de autorización y no la actividad declarada. Ambas magnitudes divergen
tanto más cuanto mayor sea la actividad no autorizada, de modo que en el caso extremo un
municipio con abundante oferta irregular aparecería como el menos saturado.

El tratamiento adoptado consiste en clasificar esta fuente con la etiqueta `no_comparable` y
excluirla de todos los índices que dependen de la saturación. La alternativa de incorporarla
como si fuese equivalente al resto habría situado a Madrid entre los municipios con mayor
oportunidad de inversión, conclusión manifiestamente falsa.

## 4.3. Galicia: la decisión de trabajar a resolución municipal

El Registro de Empresas e Actividades Turísticas de la Xunta de Galicia publica 28.465
viviendas de uso turístico, de las cuales únicamente 212 —el 0,7 %— incorporan coordenadas
utilizables. Ante esa carencia se ensayaron los dos geocodificadores oficiales españoles sobre
una misma muestra aleatoria de 300 direcciones, empleando idéntica semilla, idéntica limpieza
previa y comparación pareada dirección a dirección.

El servicio del Catastro resolvió el 45,0 % de la muestra. Cartociudad, del Instituto
Geográfico Nacional, resolvió el 26,7 %. La unión de ambos alcanzó el 57,0 %, y en los casos
resueltos por los dos servicios la concordancia fue buena: mediana de 38 metros de discrepancia
y percentil 90 de 229 metros.

Lo determinante, sin embargo, no fue la magnitud de la cobertura sino su **sesgo**. Los fallos
no se distribuyen al azar: se concentran en los topónimos rurales dispersos característicos del
poblamiento gallego —del tipo «LUGAR DE PEREIRIÑA» o «LG. SEÑORANS S/N»—, que carecen de vía y
número y que ninguno de los dos servicios, indexados por callejero y portal, puede resolver.
Geocodificar habría producido un mapa con Vigo, A Coruña y Santiago, y sin el rural gallego,
que es precisamente donde las Rías Baixas concentran mayor presión turística. Ese mapa
afirmaría «aquí no hay presión turística» cuando lo que en realidad expresa es «aquí no se supo
ubicar la oferta».

La decisión adoptada fue renunciar a la geocodificación y explotar Galicia a **resolución
municipal**, nivel en el que el dato es sólido: el concello consta en el 100 % de los registros
y las plazas en el 98,8 %, con un total de 156.026 plazas declaradas distribuidas en 306
concellos con oferta registrada. Los 7 concellos restantes que el INE reconoce y el registro no
recoge se cargan como cero real, no como ausencia de dato, puesto que sí se sabe que no tienen
viviendas registradas.

Esta decisión obligó a introducir en el modelo una segunda ruta de entrada hacia la tabla de
agregados —la carga directa del agregado oficial—, coexistente con el cruce espacial. Se detalla
también un aspecto que resultó crítico para la integridad del cruce: tres concellos figuran en
el registro bajo denominación alternativa o histórica («CANGAS DE MORRAZO», «O CASTRO DE
CALDELAS», «ALFOZ DO CASTRODOURO») que no coincide con la del INE. Resolverlos mediante un
diccionario de equivalencias verificado permitió cerrar el cruce en 313 de 313 concellos; sin
él, se habrían perdido silenciosamente 699 viviendas, 651 de ellas en un municipio costero de
las Rías Baixas.

## 4.4. Un caso de validación crítica: el índice que recomendaba invertir en Peñíscola

Durante la fase de cálculo de indicadores se detectó que el sistema situaba al municipio de
Peñíscola entre las principales oportunidades de inversión turística. Peñíscola es uno de los
destinos de costa más saturados del litoral mediterráneo, de modo que el resultado no era una
sorpresa analítica sino un síntoma de error. Su investigación reveló tres fallos encadenados,
cuya documentación se incorpora aquí por su valor metodológico.

**Primer fallo: dependencia del recuento respecto de la geocodificación.** Las viviendas de uso
turístico se contabilizaban por su posición geográfica, mediante cruce espacial. La
geocodificación de la Comunitat Valenciana estaba incompleta porque el servicio del Catastro
había interrumpido las consultas, y esa interrupción no fue aleatoria: el proceso recorría las
parcelas en el orden del fichero, ordenado por provincia y municipio, de modo que dejó
municipios enteros sin geolocalizar. Peñíscola presentaba 3.016 viviendas registradas y ninguna
geolocalizada; Oropesa del Mar, 3.198 y ninguna; Cullera, 1.449 y ninguna. La provincia de
Castellón quedó al 50,1 % frente al 83 % de Valencia.

La corrección fue de diseño, no de parámetros: el municipio consta en el 100 % de los registros
oficiales, luego el agregado municipal nunca debió depender de la geolocalización. Reescrita la
agregación para contar por municipio declarado, se recuperaron 60.450 viviendas y Peñíscola pasó
de aparecer como oportunidad a encabezar el ranking de saturación.

**Segundo fallo: los nombres sin resolver se convertían en ceros.** Tras la primera corrección,
municipios como Alcúdia o Guía de Isora seguían encabezando el índice de oportunidad con cero
viviendas registradas, cifra implausible en destinos consolidados. La causa estaba en el cruce
por topónimo: el INE escribe «Balears, Illes» donde el registro balear escribe «Illes Balears»,
mismo nombre en distinto orden. El emparejamiento fallaba y el fallo se almacenaba como cero. La
corrección consistió en generar claves de cruce insensibles al orden de las palabras, lo que
elevó la resolución balear del 89,8 % al 100 %, y en introducir una salvaguarda: cuando una
fuente deja registros sin resolver, los municipios de esa comunidad con recuento cero se marcan
como ausencia de dato y no como cero, puesto que podrían albergar las viviendas no asignadas.

**Tercer fallo: el índice de demanda medía urbanidad, no turismo.** La demanda potencial se
construía sobre la densidad de servicios por kilómetro cuadrado, magnitud que premia a cualquier
municipio denso. El resultado eran barrios dormitorio del área metropolitana de Valencia
—Catarroja, Quart de Poblet, Xirivella— encabezando el índice de oportunidad turística.
Catarroja, con 0,52 servicios por cada mil habitantes, obtenía una puntuación de demanda de 90,7
frente a los 83,7 de Cangas de Onís, que presenta 14,97. La corrección consistió en desplazar el
peso principal del índice hacia los servicios **por habitante**, magnitud que sí discrimina
especialización turística: un municipio con más hostelería de la que su población justifica está
atendiendo visitantes.

El valor metodológico de este episodio reside en que ninguno de los tres fallos producía un
error visible. El sistema no fallaba, no emitía avisos y devolvía cifras verosímiles; sencillamente
recomendaba invertir donde procedía contener. Ello refuerza la conveniencia de validar los
indicadores contra el conocimiento experto del dominio antes de construir cualquier
visualización sobre ellos.

## 4.5. Tratamiento de datos personales

El fichero del REAT gallego concatena dos bloques con esquemas distintos. El segundo, de 12.279
filas correspondientes a la provincia de Pontevedra, incluye para cada vivienda el nombre y
apellidos del titular, su número de documento de identidad y su domicilio particular. Se
verificó que 10.790 de esas filas contienen un patrón de DNI y que el domicilio del titular
difiere del de la vivienda en 4.752 casos, lo que confirma que se trata de un dato de la persona
y no del inmueble.

El sistema aplica el principio de **minimización** previsto en el artículo 5.1.c del Reglamento
General de Protección de Datos. De ese bloque se leen exclusivamente las posiciones no
personales —signatura, tipo, plazas, fecha de alta, dirección del establecimiento, código
postal, municipio y provincia—, mientras que las posiciones correspondientes al titular, su
documento y su domicilio no se leen, no se copian a ninguna estructura intermedia y no alcanzan
ni el almacenamiento ni los registros de ejecución. A diferencia del resto de fuentes, el fichero
original no se conserva: lo que se almacena es ya la versión depurada. Se excluye asimismo la
denominación comercial, dado que en una vivienda de uso turístico suele coincidir con el nombre
del propietario.

Se verificó tras la extracción la ausencia de coincidencias de patrón de DNI o NIE en los tres
ficheros resultantes. Conviene subrayar el criterio subyacente: que un organismo publique un dato
como abierto no convierte su tratamiento posterior en lícito ni en necesario, y un análisis de
densidad territorial de oferta turística se resuelve con ubicación, plazas y municipio.

## 4.6. La distinción entre cero real y ausencia de dato

La limitación de cobertura más relevante del trabajo es que once comunidades y ciudades autónomas
no publican registro de viviendas de uso turístico en formato reutilizable: Aragón, Cantabria,
Castilla-La Mancha, Castilla y León, Ceuta, Extremadura, La Rioja, Melilla, Navarra, el Principado
de Asturias y la Región de Murcia. En ellas, la única información disponible procede de
OpenStreetMap, con el sesgo descrito en el apartado 4.1.

De los 8.132 municipios, 2.034 disponen de registro oficial y 6.097 carecen de él. Sobre esa
distribución, el riesgo metodológico principal no es la falta de datos —que es explícita y
declarable— sino su confusión con el valor cero. Un municipio sin registro publicado cuyo
indicador de saturación se almacenase como cero aparecería en el sistema como territorio sin
presión turística y, por tanto, como oportunidad de inversión.

La solución adoptada consiste en una columna `origen_agregacion` que acompaña a cada bloque de
contadores y toma tres valores: `join_espacial` cuando el dato procede del recuento de puntos
contenidos en el polígono municipal, `municipal_directo` cuando procede del agregado oficial
declarado, y `sin_dato` cuando esa capa no cubre ese territorio. Esta columna se propaga a los
indicadores, que no se calculan donde el origen es `sin_dato`, y a la visualización, donde los
municipios sin dato reciben un color neutro propio y quedan explícitamente fuera de la escala
cromática de saturación.

## 4.7. Otras limitaciones declaradas

La capa de **camping** procedente de OpenStreetMap presenta el campo de capacidad en una fracción
mínima de sus 3.410 registros —en la prueba realizada sobre Baleares, uno de veinticuatro— y la
etiqueta `camp_site` agrupa indistintamente camping comercial y zonas de acampada libre. En
consecuencia, la capa se considera válida para contar establecimientos y describir su distribución
territorial, pero no para estimar capacidad de alojamiento.

La capa de **restauración** comparte etiquetado entre el establecimiento orientado al visitante y
el de uso local, por lo que mide densidad de hostelería y no especialización turística.

El **indicador de saturación total**, que suma plazas de vivienda turística y plazas hoteleras,
sólo puede calcularse en los 74 municipios que disponen simultáneamente de registro de VUT y de
condición de punto turístico en la EOH. Fuera de ese conjunto, la dimensión hotelera se conoce
únicamente a escala provincial. Se ha optado deliberadamente por **no prorratear** la cifra
provincial entre los municipios de la provincia, dado que repartir la capacidad hotelera de
Alicante entre sus municipios atribuiría oferta hotelera a poblaciones del interior que carecen de
ella, y ese error se propagaría al indicador con apariencia de dato.

[PENDIENTE: cita bibliográfica sobre calidad de datos en OpenStreetMap]
[PENDIENTE: referencia normativa completa del RGPD y de la LOPDGDD]
