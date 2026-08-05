# TFM — Dashboard de Inteligencia Territorial Turística (España)

## Objetivo

Herramienta de análisis territorial turístico para **gestores de destino** (administraciones
públicas, DMOs, consorcios turísticos), no para inversores individuales en alquiler vacacional.
El planteamiento es el de un "AirDNA para gestores de destino": en vez de optimizar el retorno
de un piso concreto, el dashboard mira el territorio como un sistema.

Busca distinguir dos situaciones opuestas a nivel municipal/comarcal:

- **Zonas con potencial de inversión turística**: demanda alta o creciente, oferta insuficiente
  para absorberla y accesibilidad adecuada.
- **Zonas saturadas**: densidad de oferta elevada, señales de sobreturismo (presión sobre
  vivienda y servicios, ratio plazas/habitante) y satisfacción del visitante decreciente.

El resultado esperado es un cuadro de mando geográfico que ayude a decidir dónde promover
inversión y dónde aplicar medidas de contención.

## Estructura del proyecto

```
tfm-tui-dashboard/
├── data/
│   ├── raw/            # Datos crudos tal cual salen de cada fuente (no versionados)
│   └── processed/      # Datos limpios y listos para cargar en la BD (no versionados)
├── etl/
│   ├── extract/
│   │   ├── extract_osm.py                # OSM alojamientos: una CCAA
│   │   ├── extract_osm_batch.py          # OSM alojamientos: toda España
│   │   ├── _capa_osm.py                  # Base común de las capas temáticas
│   │   ├── extract_osm_restauracion.py   # Restaurantes, cafeterías, bares
│   │   ├── extract_osm_atracciones.py    # Atracciones, museos, monumentos, miradores
│   │   ├── extract_osm_transporte.py     # Estaciones y nodos de transporte
│   │   └── extract_vut_oficial.py        # Registros oficiales de VUT
│   ├── transform/
│   │   └── geocode_direcciones.py    # Geocodificación con Nominatim
│   └── load/
│       ├── db.py                 # Conexión a Railway (DATABASE_URL + SQLAlchemy)
│       └── init_db.py            # Habilita PostGIS y aplica db/schema.sql
├── db/
│   └── schema.sql      # Esquema de la base de datos
├── dashboard/
│   └── app.py          # Aplicación Streamlit (pendiente)
├── notebooks/          # Exploración y validación de datos
├── docs/               # Memoria del TFM, diagramas, notas metodológicas
├── .env                # DATABASE_URL de Railway (no versionado)
└── requirements.txt
```

## Estado actual

Fase 1 — extracción. Dos fuentes ya extraídas:

- **OSM**, toda España (17 CCAA + Ceuta y Melilla): 25.271 alojamientos.
- **Registros oficiales de VUT**, 6 de 7 fuentes: 274.705 registros.

La base de datos está alojada en **Railway** (PostgreSQL + PostGIS) y el esquema se aplica
con `etl/load/init_db.py`.

Pendiente: la fusión de ambas fuentes, la geocodificación de los registros sin coordenadas,
la carga a la base de datos y el dashboard.

## Puesta en marcha

### 1. Requisitos

- Python 3.11+
- Una cuenta en [Railway](https://railway.app) (la base de datos está alojada allí)

### 2. Crear la base de datos en Railway

La base de datos vive en Railway, no en local: así el dashboard puede desplegarse más
adelante sin cambiar nada de la configuración.

**Importante**: el servicio "PostgreSQL" que Railway ofrece por defecto **no incluye
PostGIS**, y este proyecto lo necesita para todo el análisis espacial. Hay que desplegar
una imagen que sí lo traiga:

1. En Railway: **New Project** → **Empty Project**.
2. Dentro del proyecto: **New** → **Docker Image** → imagen `postgis/postgis:16-3.4`.
3. En el servicio creado, pestaña **Variables**, define:
   - `POSTGRES_USER` (p. ej. `tui_user`)
   - `POSTGRES_PASSWORD` (una contraseña larga)
   - `POSTGRES_DB` (p. ej. `tui_dashboard`)
4. Pestaña **Settings** → **Networking** → **Generate Domain / TCP Proxy** sobre el puerto
   `5432`. Railway devolverá un host y un puerto públicos.
5. Construye la cadena de conexión con esos datos:

   ```
   postgresql://POSTGRES_USER:POSTGRES_PASSWORD@HOST_PUBLICO:PUERTO_PUBLICO/POSTGRES_DB
   ```

> Si en su lugar usas el servicio PostgreSQL estándar de Railway, copia su
> `DATABASE_PUBLIC_URL` desde la pestaña **Variables** — pero `init_db.py` fallará al
> crear la extensión PostGIS y te lo indicará con instrucciones. Usa siempre la URL
> **pública**: la interna (`*.railway.internal`) sólo resuelve dentro de la red de Railway
> y no funciona desde tu máquina.

### 3. Configuración local

```bash
cp .env.example .env
```

Pega la cadena del paso anterior en la variable `DATABASE_URL` de `.env`. Es la **única**
variable de configuración del proyecto. `.env` está en `.gitignore`: no se sube al
repositorio.

### 4. Instalar dependencias de Python

```bash
python -m venv .venv
source .venv/bin/activate      # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> `geopandas` puede dar problemas de compilación en Windows con `pip`. Si falla, la vía
> cómoda es instalarlo con conda (`conda install -c conda-forge geopandas`). El script de
> extracción sólo necesita `requests`, así que no bloquea esta primera fase.

### 5. Aplicar el esquema en Railway

```bash
python etl/load/init_db.py
```

El script se conecta usando `DATABASE_URL`, habilita PostGIS (`CREATE EXTENSION IF NOT
EXISTS postgis`) y aplica `db/schema.sql`. Es idempotente: vuelve a lanzarlo cada vez que
modifiques el esquema.

Para comprobar el estado sin tocar nada (versión de PostGIS, tablas existentes, número de
filas):

```bash
python etl/load/init_db.py --solo-comprobar
```

Y para verificar sólo la conexión:

```bash
python etl/load/db.py
```

### 6. Extracción de datos

**Una sola CCAA** (útil para probar o reintentar una concreta):

```bash
python etl/extract/extract_osm.py --ccaa baleares
python etl/extract/extract_osm.py --listar-ccaa            # ver las 19 disponibles
python etl/extract/extract_osm.py --ccaa madrid --tipos hotel hostel
```

**Toda España** (17 CCAA + Ceuta y Melilla):

```bash
python etl/extract/extract_osm_batch.py
```

Opciones del modo por lotes:

```bash
python etl/extract/extract_osm_batch.py --pausa 30              # más margen para Overpass
python etl/extract/extract_osm_batch.py --saltar-existentes     # reutiliza lo ya descargado
python etl/extract/extract_osm_batch.py --ccaa madrid cataluna  # subconjunto
```

Ambos scripts descargan de la API Overpass de OpenStreetMap los elementos con
`tourism=hotel|hostel|apartment|guest_house` y guardan la respuesta cruda en
`data/raw/osm_alojamientos_<ccaa>_<fecha>.json`, con un bloque `metadata` que registra la
fecha de extracción, el código ISO usado y el recuento por tipo.

El modo por lotes añade además:

- Un consolidado final `data/raw/osm_alojamientos_espana_consolidado_<fecha>.json` con todos
  los elementos, cada uno etiquetado con su CCAA de origen.
- Una **pausa entre comunidades** (20 s por defecto) para no saturar la API.
- Orden de extracción de menor a mayor comunidad: si la API está dando problemas, se detecta
  pronto y sin haber gastado las consultas caras.
- Tolerancia a fallos: si una CCAA falla, continúa con las demás y la lista al final.
  Con `--saltar-existentes` se reintentan sólo las que faltan.

La comunidad se selecciona por su código **ISO 3166-2** (`ES-IB`, `ES-CT`, ...), más estable
que buscar por nombre, y las consultas rotan entre varias réplicas de Overpass con reintentos
porque la instancia principal devuelve `429`/`504` con cierta frecuencia.

#### Nota metodológica: respuestas degradadas de Overpass

Durante la primera ejecución por lotes, La Rioja devolvió **0 alojamientos**. No era un dato
real: Overpass había respondido `HTTP 200` con `"elements": []` (la base de datos de áreas no
estaba disponible en ese instante). La misma consulta, repetida minutos después, devolvió 195.

El riesgo es serio en una extracción por lotes: un fichero de 0 elementos se guarda como si
fuera una extracción correcta y el hueco pasa desapercibido hasta que aparece como "zona sin
oferta" en el análisis de saturación. Los scripts tratan ahora como fallo reintentable:

- las respuestas con campo `remark` (errores de ejecución que Overpass envía con código 200);
- cualquier respuesta con 0 elementos, ya que ninguna CCAA española tiene 0 alojamientos.

`--saltar-existentes` también descarta y vuelve a pedir los ficheros vacíos de ejecuciones
previas. La excepción es `--permitir-vacio` en `extract_osm.py`, para filtros `--tipos` tan
restrictivos que el vacío pueda ser legítimo.

## Resultado de la extracción (España, 04/08/2026)

19/19 comunidades extraídas sin fallos. **25.271 alojamientos** en 64 min 54 s.

| Comunidad autónoma         |  hotel | hostel | apartment | guest_house |  TOTAL |
|----------------------------|-------:|-------:|----------:|------------:|-------:|
| Andalucía                  |  2.328 |    359 |       780 |         521 |  3.988 |
| Cataluña                   |  2.053 |    382 |       329 |         407 |  3.171 |
| Castilla y León            |  1.223 |    644 |       127 |         598 |  2.592 |
| Canarias                   |  1.584 |    129 |       408 |         205 |  2.326 |
| Illes Balears              |  1.763 |     68 |       186 |         110 |  2.127 |
| Galicia                    |    929 |    589 |        94 |         440 |  2.052 |
| Comunitat Valenciana       |    856 |    167 |       167 |         170 |  1.360 |
| Comunidad de Madrid        |    712 |    165 |       113 |         308 |  1.298 |
| Principado de Asturias     |    606 |    163 |       135 |         199 |  1.103 |
| Aragón                     |    556 |    171 |       116 |         189 |  1.032 |
| Castilla-La Mancha         |    542 |    157 |        81 |         141 |    921 |
| País Vasco                 |    442 |    164 |        50 |         177 |    833 |
| Cantabria                  |    410 |    120 |       110 |         105 |    745 |
| Comunidad Foral de Navarra |    234 |    170 |        43 |         192 |    639 |
| Extremadura                |    351 |    144 |        55 |          70 |    620 |
| Región de Murcia           |    164 |     35 |        30 |          17 |    246 |
| La Rioja                   |     88 |     68 |        13 |          26 |    195 |
| Melilla                    |      7 |      5 |         0 |           1 |     13 |
| Ceuta                      |      5 |      3 |         1 |           1 |     10 |
| **TOTAL**                  | **14.853** | **3.703** | **2.838** | **3.877** | **25.271** |

Comprobaciones sobre el consolidado:

- 25.271/25.271 elementos **con coordenadas** (los `way` —9.610— y `relation` —397— se
  resuelven con `out center`, que devuelve el centroide; el resto son 15.264 `node`).
- La suma de los 19 ficheros por CCAA coincide exactamente con el consolidado.
- Ningún fichero vacío.
- 23.761/25.271 (94 %) con etiqueta `name`.
- **1 duplicado**: `relation/14768729` (Hotel Pena Trevinca), en la frontera Galicia /
  Castilla y León, aparece en ambas consultas. Son 25.270 alojamientos únicos. La
  deduplicación por `type` + `id` corresponde a la fase de transformación.
- Etiquetas útiles frecuentes para esa fase: `addr:city`, `addr:postcode`, `addr:street`,
  `stars`, `website`, `phone`, `brand`.

### Lectura preliminar

El reparto por tipo confirma el sesgo de OSM ya anticipado: el 59 % de los registros son
`hotel` y sólo el 11 % `apartment`. En un país donde la vivienda de uso turístico es el
centro del debate sobre saturación, esa proporción no refleja la realidad del mercado, sino
qué se cartografía en OSM. Baleares es el caso más claro: 1.763 hoteles frente a 186
apartamentos.

Conclusión operativa: OSM sirve como **capa geográfica base**, pero el denominador de "oferta"
para los indicadores de saturación tiene que venir de los registros oficiales autonómicos de
VUT. Ver la sección siguiente.

## Capas temáticas de OSM

Además de alojamiento, tres capas más, todas parametrizadas por CCAA con la misma mecánica
(ISO 3166-2, rotación de réplicas, reintentos ante respuesta vacía o con `remark`,
`out center`). La lógica común está en `_capa_osm.py`.

```bash
python etl/extract/extract_osm_restauracion.py --ccaa baleares
python etl/extract/extract_osm_atracciones.py  --ccaa baleares
python etl/extract/extract_osm_transporte.py   --ccaa baleares
python etl/extract/extract_osm_transporte.py   --ccaa baleares --perfil completo
```

Salida: `data/raw/osm_<capa>_<ccaa>_<fecha>.json`, mismo formato que la de alojamientos.

### Prueba en Baleares (05/08/2026)

| Capa | Elementos | Desglose |
|---|---:|---|
| Restauración | 6.070 | restaurant 3.571 · cafe 1.715 · bar 784 |
| Atracciones | 1.053 | viewpoint 695 · attraction 181 · museum 103 · monument 72 |
| Transporte (principales) | 91 | railway=station 36 · ferry_terminal 28 · bus_station 20 · halt 6 |
| Transporte (completo) | 3.009 | platform 2.631 · stop_position 187 · railway=stop 68 · … |

**Sobre el perfil de transporte.** El perfil `completo` multiplica por 33 el volumen, y el
87 % de lo que añade son `public_transport=platform`: marquesinas y andenes individuales. Para
comparar la accesibilidad *entre* destinos eso es ruido — lo que importa es dónde hay nodos de
entrada (estaciones, intercambiadores), no cuántas paradas tiene una avenida. El perfil
`principales` es el que debe usarse para el dashboard; `completo` sólo tiene sentido si en
algún momento se hace análisis intraurbano de cobertura.

Un hallazgo del perfil `principales`: aparecen 28 **terminales de ferry**, que entran por
`public_transport=station`. En un archipiélago son la puerta de entrada principal al destino,
así que conviene tratarlas explícitamente y no como un residuo de la consulta.

**Sobre restauración.** En España el bar de barrio y el restaurante turístico comparten
etiqueta. Esta capa mide densidad de hostelería en general, no oferta orientada al visitante:
sirve para comparar densidad relativa entre zonas, no como medida de especialización turística.

## Segunda fuente: registros oficiales de VUT

```bash
python etl/extract/extract_vut_oficial.py
python etl/extract/extract_vut_oficial.py --listar
python etl/extract/extract_vut_oficial.py --fuentes canarias barcelona
```

Cada organismo es una función independiente (`fuente_*`) en
[extract_vut_oficial.py](etl/extract/extract_vut_oficial.py), para poder ir añadiendo
comunidades sin tocar el resto. Salidas por fuente:

- `data/raw/vut_oficial_<slug>.<csv|json|zip>` — crudo, tal cual lo sirve el organismo.
- `data/processed/vut_normalizado_<slug>.csv` — esquema común.
- `data/processed/vut_informe_fuentes.json` — qué se obtuvo, con los campos de origen de
  cada fuente. Conserva las fuentes de ejecuciones anteriores, así que se puede relanzar
  una sola sin perder el informe completo.

**Esquema normalizado**: `id_fuente`, `nombre`, `lat`, `lon`, `direccion`, `ccaa`,
`provincia`, `municipio`, `plazas`, `fecha_registro`, `fuente`, `necesita_geocodificacion`.
La última columna marca los registros sin coordenadas utilizables, que habrá que geocodificar
a partir de la dirección postal.

### Resultado (04/08/2026)

**364.683 registros** de las 7 fuentes.

| Fuente | Ámbito | Registros | Con coord. | % |
|---|---|---:|---:|---:|
| Andalucía (OpenRTA) | CCAA completa | 168.796 | 159.880 | 94,7 % |
| Comunitat Valenciana (GVA) | CCAA completa | 89.978 | 0 | 0 % |
| Canarias (Registro General Turístico) | CCAA completa | 72.645 | 48.606 | 66,9 % |
| Mallorca (Consell de Mallorca) | Sólo Mallorca | 16.854 | 8.909 | 52,9 % |
| Barcelona (Open Data BCN) | Sólo ciudad | 10.627 | 10.627 | 100 % |
| País Vasco (REATE) | CCAA completa | 4.786 | 0 | 0 % |
| Madrid (Geoportal) | Sólo ciudad | 997 | 997 | 100 % |
| **TOTAL** | | **364.683** | **229.019** | **62,8 %** |

Comprobado sobre los normalizados: el esquema es idéntico en las 6 fuentes, el flag
`necesita_geocodificacion` es coherente con la presencia de coordenadas, y el 99,99 % de los
puntos cae dentro de su comunidad (24 registros de Andalucía y Canarias están mal
geocodificados **en origen** — direcciones de Estepona o Yaiza con coordenadas en Madrid o
Barcelona).

### Comunitat Valenciana: portal equivocado

El registro **sí es descargable**, pero no desde el portal de Turisme GVA
(`turisme.gva.es/datosabiertos`) que es el que aparece al buscar. Está en el portal general
de datos abiertos de la Generalitat:

```
https://dadesobertes.gva.es/dataset/tur-gestur-vt
```

Merece la pena dejar constancia de por qué el otro portal no sirve, porque parece una fuente
válida y no lo es:

- La página de viviendas turísticas es un formulario WordPress (`action="#!"`) resuelto en
  cliente por `recursosFormManager.js`.
- Sus desplegables se rellenan llamando a `http://desajava03.turisme.gva.es:8080/datosabiertos/…`,
  un host **interno de desarrollo** que no resuelve desde fuera. En producción los filtros de
  provincia y municipio llegan vacíos ("Todas"/"Todos").
- Aunque funcionara, su único tipo es "Bloques y conjuntos de viviendas turísticas": bloques
  y complejos, no el registro de viviendas individuales.

El fichero de `dadesobertes.gva.es` sí es el registro completo: 89.978 viviendas. No trae
coordenadas, pero incluye `ref_catastral`, que permite geocodificar contra el servicio del
Catastro con mucha más precisión que Nominatim sobre la dirección postal.

### Galicia: descargable, pero con datos personales

El **Sistema de Intelixencia Turística de Galicia** (`aei.turismo.gal`) no es sólo un visor:
publica el directorio del REAT en CSV descargable sin autenticación.

```
https://descargascdn.xunta.gal/interno/smarxa/reat_directorio-alojamientos_esp.csv
```

(El dataset equivalente en `abertos.xunta.gal` devuelve HTML, no el fichero.)

Contiene 12.281 viviendas de uso turístico, con `longitud`/`latitud` incluidas. **No está
integrado todavía en `extract_vut_oficial.py`**, por dos motivos que conviene resolver antes:

1. **El fichero mezcla dos esquemas.** Las primeras 20.451 filas siguen la cabecera declarada
   (18 campos). A partir de la línea 20456 hay 12.279 filas con 38 campos y otro esquema, que
   son justamente las VUT. Cualquier lector de CSV falla ahí.
2. **Ese segundo bloque incluye datos personales.** Nombre y apellidos del titular y su
   DNI/NIF: 10.790 de las 12.281 filas contienen un patrón de DNI. Son personas físicas.

Lo segundo no es un detalle técnico. El dashboard no necesita titular ni NIF —le bastan
ubicación, plazas y municipio— así que la integración debe leer sólo las columnas necesarias
y **no escribir nunca los campos personales a disco**. Volcarlos a la base de datos o a un
panel sería un problema de protección de datos evitable, y conviene decidirlo de forma
explícita antes de tocar esta fuente.

### Avisos sobre estas fuentes

Los registros **no son homogéneos entre sí** y no deben agregarse sin más:

- **Cobertura desigual.** Andalucía, Canarias y País Vasco son autonómicos; Mallorca,
  Barcelona y Madrid son insulares o municipales. Baleares sin Menorca, Ibiza ni Formentera
  (cada consell insular publica por su cuenta) y Cataluña sin nada fuera de Barcelona.
- **Universos distintos.** Madrid publica *licencias urbanísticas concedidas*, no el registro
  turístico autonómico: por eso salen 997 frente a las decenas de miles de anuncios activos
  en plataformas. Comparar ese número con el de Barcelona sería un error.
- **Sin coordenadas.** El País Vasco sólo publica dirección postal: sus 4.786 registros
  necesitan geocodificación antes de servir para nada espacial. En Canarias, 23.979 filas
  traen `(0,0)`, que es ausencia de coordenada, no un punto en el golfo de Guinea.
- **Formatos decimales mezclados.** OpenRTA combina punto y coma decimal en la *misma*
  columna (`539165.3196` y `345032,81`). Convertir sin normalizar el separador descarta
  silenciosamente el 94 % de las coordenadas; el script lo tiene en cuenta.
- **Andalucía es pesada.** El volcado son ~325 MB de JSON con todas las tipologías (hoteles,
  guías, restauración); el filtrado a VUT se hace en local porque el parámetro `object_type`
  del endpoint `/search` se acepta pero no filtra. Necesita ~4 GB de RAM y descarga con
  reintentos. La variante `format=csv` está mal formada (pipes sin escapar en texto libre),
  por eso se usa el JSON.

## Modelo de datos

Tabla `establecimientos_turisticos`: identidad de la fuente (`fuente_dato` + `id_fuente`, con
restricción de unicidad para poder reejecutar el ETL sin duplicar), clasificación
(`tipo` como ENUM alojamiento/restauración/atracción, más `subtipo` con el valor original de la
fuente), ubicación (`lat`, `lon` y `geom` de tipo `geometry(Point, 4326)` con índice GIST),
adscripción territorial (`ccaa`, `provincia`, `municipio`) y señales de demanda (`rating`,
`num_reviews`).

Un trigger rellena `geom` a partir de `lat`/`lon`, de modo que el ETL sólo necesita insertar
las coordenadas.

`init_db.py` envía `schema.sql` al servidor en una sola llamada en lugar de trocearlo por `;`,
porque partir el fichero rompería los bloques `DO $$ ... $$` y el cuerpo de la función del
trigger.

## Fuentes de datos

- **OpenStreetMap** (API Overpass) — oferta de alojamiento georreferenciada. Extraída para
  toda España (25.271 registros).
- **Registros oficiales autonómicos de VUT** — Andalucía, Canarias, Mallorca, Barcelona,
  Madrid y País Vasco (274.705 registros). Comunitat Valenciana pendiente de descarga manual.
- Pendientes de incorporar: INE (población, ocupación hotelera, viviendas turísticas),
  el resto de registros autonómicos de VUT, y una fuente de reseñas para poblar
  `rating` / `num_reviews`.

### Sobre la cobertura de OSM

OSM no es un registro oficial: la cobertura es desigual entre territorios y tiende a
infrarrepresentar el alojamiento no hotelero (apartamentos turísticos, viviendas de uso
turístico). Los datos extraídos lo confirman —sólo un 11 % de registros `apartment`—, y el
sesgo no es uniforme entre comunidades, lo que impide compararlas directamente por densidad
de oferta usando sólo OSM.

Sirve muy bien como capa geográfica base, pero el cálculo de densidad de oferta debe
contrastarse con los registros oficiales autonómicos de VUT antes de sacar cualquier
conclusión sobre saturación. Conviene dejarlo escrito también en la memoria del TFM como
limitación metodológica declarada.

## Próximos pasos

1. Levantar el servicio PostGIS en Railway y aplicar el esquema con `init_db.py`.
2. `etl/transform/`: deduplicar por `type` + `id`, normalizar campos, mapear `tourism=*` a
   `tipo`/`subtipo` y asignar municipio/provincia por cruce espacial con los límites
   administrativos (la CCAA ya viene de la consulta).
3. `etl/load/`: carga del consolidado en PostGIS.
4. Geocodificar los registros de VUT sin coordenadas (45.686, sobre todo País Vasco y
   Canarias) a partir de su dirección postal.
5. Fusionar OSM y VUT resolviendo el solape: en Andalucía, Canarias, Mallorca, Barcelona y
   Madrid los mismos inmuebles pueden estar en ambas fuentes.
6. Completar la cobertura de VUT: Comunitat Valenciana (manual), resto de Cataluña, Menorca,
   Ibiza y Formentera, y las CCAA aún no cubiertas.
7. Incorporar indicadores de demanda y población (INE) para construir los ratios de saturación.
8. `dashboard/app.py`: mapa coroplético e indicadores en Streamlit.
