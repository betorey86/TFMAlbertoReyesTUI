# 7. Bibliografía y anexos

## 7.1. Bibliografía

> **Estado:** pendiente. No se incluye ninguna referencia que no haya sido verificada, para
> evitar citas inexactas. Este apartado recoge únicamente las **fuentes de datos** efectivamente
> utilizadas, que sí constan documentadas en el proyecto.

[PENDIENTE: referencias académicas sobre capacidad de carga turística y sobreturismo]
[PENDIENTE: referencias sobre Destinos Turísticos Inteligentes]
[PENDIENTE: referencias sobre calidad de la cartografía colaborativa]
[PENDIENTE: fijar norma de citación exigida por la titulación]

## 7.2. Fuentes de datos utilizadas

Todas las direcciones han sido verificadas mediante acceso programático durante el desarrollo
del trabajo.

**Base territorial y demográfica**

- Instituto Nacional de Estadística. *Cartografía del seccionado censal*, edición 2026.
  `https://www.ine.es/prodyser/cartografia/seccionado_2026.zip`
- Instituto Nacional de Estadística. *Padrón municipal continuo*, revisión 2025.
  `https://www.ine.es/pob_xls/pobmun.zip`

**Estadística turística**

- Instituto Nacional de Estadística. *Encuesta de Ocupación Hotelera* (IOE 30235), tablas 2066
  y 2076, accedidas a través de la API Tempus3. `https://servicios.ine.es/wstempus/js/ES/`

**Registros oficiales de vivienda de uso turístico**

- Junta de Andalucía. *Registro de Turismo de Andalucía (OpenRTA)*.
  `https://datos.juntadeandalucia.es/api/v0/openrta/`
- Generalitat Valenciana. *Lista de viviendas turísticas*. `https://dadesobertes.gva.es/`
- Gobierno de Canarias. *Registro General Turístico*. `https://datos.canarias.es/`
- Xunta de Galicia. *Registro de Empresas e Actividades Turísticas (REAT)*.
  `https://descargascdn.xunta.gal/interno/smarxa/`
- Consell de Mallorca. *Registre d'Habitatges Turístics*, vía Catàleg de Dades Obertes de les
  Illes Balears. `https://intranet.caib.es/opendatacataleg/`
- Ajuntament de Barcelona. *Habitatges d'ús turístic*. `https://opendata-ajuntament.barcelona.cat/`
- Open Data Euskadi. *Censo de viviendas turísticas (REATE)*. `https://opendata.euskadi.eus/`
- Ayuntamiento de Madrid. *Viviendas de uso turístico con licencia*, Geoportal.
  `https://geoportal.madrid.es/`

**Cartografía y geocodificación**

- OpenStreetMap, a través de la API Overpass. `https://overpass-api.de/api/interpreter`
- Dirección General del Catastro. *Servicios web de cartografía catastral* (OVCCoordenadas y
  OVCCallejero). `https://ovc.catastro.meh.es/ovcservweb/`
- Instituto Geográfico Nacional. *Cartociudad*, servicio de geocodificación.
  `https://www.cartociudad.es/geocoder/api/geocoder/`
- Nominatim, servicio de geocodificación de la OpenStreetMap Foundation.

## 7.3. Anexos

### Anexo A. Inventario de datos

Se incorpora como anexo el documento `docs/inventario_datos.md`, generado automáticamente por el
script `etl/transform/generar_inventario.py` mediante lectura directa de los ficheros de datos.
Recoge una fila por capa, fuente y territorio, con volúmenes, cobertura de geolocalización,
resolución espacial, nivel de confianza y limitaciones declaradas, así como una sección
específica sobre las trampas metodológicas identificadas.

### Anexo B. Modelo de datos

[PENDIENTE: incorporar el esquema de `db/schema.sql` con su diagrama entidad-relación]

El modelo se organiza en dos niveles: una tabla de establecimientos individuales
georreferenciados y una tabla de agregados municipales, sobre una base de municipios del INE.
Incluye la vista `indicadores_municipales`, que calcula los indicadores normalizados sin
materializarlos, de modo que no puedan desincronizarse de sus denominadores.

### Anexo C. Estructura del repositorio y reproducibilidad

[PENDIENTE: redacción]

El proyecto es reproducible en su totalidad ejecutando la cadena de extracción, transformación y
cálculo documentada en el `README.md`. Los scripts principales son:

| Etapa | Script |
|---|---|
| Base municipal del INE | `etl/extract/extract_ine_municipios.py` |
| Extracción de OpenStreetMap | `etl/extract/extract_osm_batch.py` |
| Registros oficiales de VUT | `etl/extract/extract_vut_oficial.py` |
| Encuesta de Ocupación Hotelera | `etl/extract/extract_eoh_ine.py` |
| Geocodificación (Catastro) | `etl/transform/geocode_catastro_valencia.py` |
| Geocodificación (Nominatim) | `etl/transform/geocode_direcciones.py` |
| Agregación municipal | `etl/transform/agregar_municipal.py` |
| Cálculo de indicadores | `etl/transform/calcular_indicadores.py` |
| Cuadro de mando | `dashboard/app.py` |

### Anexo D. Capturas del cuadro de mando

[PENDIENTE: capturas de las cuatro vistas del dashboard analítico]
