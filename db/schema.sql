-- TFM - Dashboard de Inteligencia Territorial Turística (España)
--
-- Modelo de dos niveles:
--
--   municipios              Unidad territorial de análisis (INE). Aporta el denominador
--                           de los indicadores: población y superficie.
--   establecimientos        Nivel de detalle. Un punto por establecimiento, con su
--                           municipio resuelto por join espacial.
--   agregados_municipales   Nivel de comparación. Un registro por municipio con los
--                           contadores de cada capa de oferta.
--
-- La tabla de agregados admite DOS rutas de entrada hacia la misma fila:
--
--   (a) join espacial   — se cuentan los establecimientos que caen dentro del polígono
--                         municipal. Es la vía del territorio con dato de punto.
--   (b) carga directa   — el agregado llega ya calculado desde el registro oficial. Es la
--                         vía de Galicia, cuyas VUT sólo tienen resolución municipal.
--
-- Por eso cada bloque de contadores lleva su propia columna de origen: sin ella no habría
-- forma de saber si un cero significa "no hay oferta" o "esta capa no cubre este
-- territorio", que es la confusión que más daño haría a las conclusiones.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------
-- Tipos
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_establecimiento') THEN
        CREATE TYPE tipo_establecimiento AS ENUM ('alojamiento', 'restauracion', 'atraccion', 'transporte');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'origen_agregacion') THEN
        -- 'join_espacial'      contado desde los puntos contenidos en el municipio
        -- 'municipal_directo'  cargado ya agregado desde el registro oficial (Galicia)
        -- 'sin_dato'           esta capa no cubre este territorio: NO es un cero real
        CREATE TYPE origen_agregacion AS ENUM ('join_espacial', 'municipal_directo', 'sin_dato');
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 1. Municipios (INE)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS municipios (
    -- 5 dígitos: 2 de provincia + 3 de municipio. Es la clave de cruce de todo el
    -- proyecto. Nunca se cruza por nombre: el INE escribe "Coruña, A" donde los
    -- registros autonómicos escriben "A CORUÑA".
    codigo_ine        CHAR(5) PRIMARY KEY,
    nombre            TEXT NOT NULL,
    codigo_provincia  CHAR(2) NOT NULL,
    provincia         TEXT NOT NULL,
    codigo_ccaa       CHAR(2),
    ccaa              TEXT NOT NULL,

    poblacion         INTEGER,
    superficie_km2    NUMERIC(12,4),

    geometria         geometry(MultiPolygon, 4326) NOT NULL,

    fuente            TEXT NOT NULL DEFAULT 'INE - seccionado censal + padrón municipal',
    actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT municipios_poblacion_valida  CHECK (poblacion IS NULL OR poblacion >= 0),
    CONSTRAINT municipios_superficie_valida CHECK (superficie_km2 IS NULL OR superficie_km2 > 0)
);

CREATE INDEX IF NOT EXISTS idx_municipios_geom      ON municipios USING GIST (geometria);
CREATE INDEX IF NOT EXISTS idx_municipios_provincia ON municipios (codigo_provincia);
CREATE INDEX IF NOT EXISTS idx_municipios_ccaa      ON municipios (ccaa);

COMMENT ON COLUMN municipios.superficie_km2 IS
    'Calculada sobre el polígono en EPSG:25830; el seccionado del INE no la publica.';

-- ---------------------------------------------------------------------------
-- 2. Establecimientos (detalle, un punto por registro)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS establecimientos (
    id                BIGSERIAL PRIMARY KEY,

    -- Identificador en la fuente original ("node/1234567" en OSM, la signatura en los
    -- registros oficiales). Junto con fuente_dato permite reejecutar el ETL sin duplicar.
    id_fuente         TEXT,
    fuente_dato       TEXT NOT NULL,

    nombre            TEXT,
    tipo              tipo_establecimiento NOT NULL,
    -- Valor original de la fuente: hotel, apartment, camp_site, restaurant, museum...
    subtipo           TEXT,

    lat               DOUBLE PRECISION,
    lon               DOUBLE PRECISION,
    geom              geometry(Point, 4326),

    -- Se rellena por join espacial contra municipios.geometria. Queda NULL cuando el punto
    -- no cae en ningún municipio (islas menores, errores de coordenada en origen).
    codigo_ine        CHAR(5) REFERENCES municipios(codigo_ine) ON DELETE SET NULL,

    -- Adscripción declarada por la fuente. Se conserva junto a codigo_ine para poder
    -- detectar discrepancias entre lo que dice el registro y dónde cae el punto.
    ccaa              TEXT,
    provincia         TEXT,
    municipio         TEXT,

    plazas            INTEGER,
    rating            NUMERIC(3,2),
    num_reviews       INTEGER,

    fecha_registro    DATE,
    fecha_extraccion  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT establecimientos_rating_valido
        CHECK (rating IS NULL OR (rating >= 0 AND rating <= 5)),
    CONSTRAINT establecimientos_num_reviews_valido
        CHECK (num_reviews IS NULL OR num_reviews >= 0),
    CONSTRAINT establecimientos_plazas_validas
        CHECK (plazas IS NULL OR plazas >= 0),
    CONSTRAINT establecimientos_fuente_unica
        UNIQUE (fuente_dato, id_fuente)
);

CREATE INDEX IF NOT EXISTS idx_establecimientos_geom    ON establecimientos USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_establecimientos_ine     ON establecimientos (codigo_ine);
CREATE INDEX IF NOT EXISTS idx_establecimientos_tipo    ON establecimientos (tipo);
CREATE INDEX IF NOT EXISTS idx_establecimientos_fuente  ON establecimientos (fuente_dato);

-- Mantiene geom sincronizada con lat/lon: el ETL sólo necesita insertar las coordenadas.
CREATE OR REPLACE FUNCTION set_geom_from_latlon()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_geom ON establecimientos;
CREATE TRIGGER trg_set_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON establecimientos
    FOR EACH ROW EXECUTE FUNCTION set_geom_from_latlon();

-- ---------------------------------------------------------------------------
-- 3. Agregados municipales (comparación entre territorios)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agregados_municipales (
    codigo_ine              CHAR(5) PRIMARY KEY REFERENCES municipios(codigo_ine) ON DELETE CASCADE,

    -- --- Oferta de alojamiento ---
    n_alojamientos_osm      INTEGER NOT NULL DEFAULT 0,
    n_vut_oficial           INTEGER NOT NULL DEFAULT 0,
    plazas_vut_declaradas   INTEGER,
    n_camping               INTEGER NOT NULL DEFAULT 0,

    -- --- Servicios y recursos ---
    n_restauracion          INTEGER NOT NULL DEFAULT 0,
    n_atracciones           INTEGER NOT NULL DEFAULT 0,
    n_transporte            INTEGER NOT NULL DEFAULT 0,

    -- --- Trazabilidad de cada bloque ---
    -- Sin esto, un 0 en n_vut_oficial es ambiguo: puede ser "este municipio no tiene VUT"
    -- o "esta comunidad no publica registro". Son cosas distintas y no deben pintarse
    -- igual en el mapa ni entrar igual en un indicador.
    origen_vut              origen_agregacion NOT NULL DEFAULT 'sin_dato',
    origen_osm              origen_agregacion NOT NULL DEFAULT 'sin_dato',
    fuente_vut              TEXT,

    -- Cobertura del registro de VUT sobre este territorio, tal como se documenta en
    -- docs/inventario_datos.md: 'completa', 'parcial', 'no_comparable', 'municipal',
    -- 'sin_registro'.
    cobertura_vut           TEXT,

    -- Porcentaje de VUT del municipio que declaran plazas. Si es bajo, plazas_vut_declaradas
    -- infraestima y el ratio de saturación no debe presentarse sin la advertencia.
    pct_vut_con_plazas      NUMERIC(5,2),

    calculado_en            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT agregados_no_negativos CHECK (
        n_alojamientos_osm >= 0 AND n_vut_oficial >= 0 AND n_camping >= 0
        AND n_restauracion >= 0 AND n_atracciones >= 0 AND n_transporte >= 0
        AND (plazas_vut_declaradas IS NULL OR plazas_vut_declaradas >= 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_agregados_origen_vut ON agregados_municipales (origen_vut);
CREATE INDEX IF NOT EXISTS idx_agregados_cobertura  ON agregados_municipales (cobertura_vut);

COMMENT ON TABLE agregados_municipales IS
    'Un registro por municipio. Alimenta los indicadores del dashboard. Admite dos rutas '
    'de entrada: join espacial desde establecimientos, o carga directa del agregado '
    'oficial (caso de Galicia).';

-- ---------------------------------------------------------------------------
-- 4. Vista de indicadores
-- ---------------------------------------------------------------------------

-- Los indicadores se calculan en la vista, no se almacenan: dependen de población y
-- superficie, que cambian con cada padrón, y así no hay riesgo de que el valor guardado
-- deje de corresponderse con su denominador.
CREATE OR REPLACE VIEW indicadores_municipales AS
SELECT
    m.codigo_ine,
    m.nombre,
    m.provincia,
    m.ccaa,
    m.poblacion,
    m.superficie_km2,

    a.n_alojamientos_osm,
    a.n_vut_oficial,
    a.plazas_vut_declaradas,
    a.n_camping,
    a.n_restauracion,
    a.n_atracciones,
    a.n_transporte,

    -- Saturación: oferta por habitante. El indicador central del trabajo.
    CASE WHEN m.poblacion > 0
         THEN ROUND(1000.0 * a.plazas_vut_declaradas / m.poblacion, 2)
    END AS plazas_vut_por_1000_hab,
    CASE WHEN m.poblacion > 0
         THEN ROUND(1000.0 * a.n_vut_oficial / m.poblacion, 2)
    END AS vut_por_1000_hab,

    -- Densidad territorial: oferta por km².
    CASE WHEN m.superficie_km2 > 0
         THEN ROUND(a.n_vut_oficial / m.superficie_km2, 3)
    END AS vut_por_km2,
    CASE WHEN m.superficie_km2 > 0
         THEN ROUND(a.n_restauracion / m.superficie_km2, 3)
    END AS restauracion_por_km2,

    a.origen_vut,
    a.cobertura_vut,
    a.pct_vut_con_plazas
FROM municipios m
LEFT JOIN agregados_municipales a USING (codigo_ine);

COMMENT ON VIEW indicadores_municipales IS
    'Indicadores normalizados por habitante y por km². Los municipios con '
    'origen_vut = ''sin_dato'' NO deben interpretarse como ausencia de oferta.';
