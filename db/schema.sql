-- TFM - Dashboard de Inteligencia Territorial Turística (España)
-- Esquema base. Se ejecuta automáticamente al crear el contenedor por primera vez.

CREATE EXTENSION IF NOT EXISTS postgis;

-- Tipo de establecimiento: mantenemos un ENUM para forzar consistencia entre fuentes.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_establecimiento') THEN
        CREATE TYPE tipo_establecimiento AS ENUM ('alojamiento', 'restauracion', 'atraccion');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS establecimientos_turisticos (
    id                BIGSERIAL PRIMARY KEY,

    -- Identificador en la fuente original (p.ej. "node/1234567" en OSM).
    -- Junto con fuente_dato permite reejecutar el ETL sin duplicar filas.
    id_fuente         TEXT,
    fuente_dato       TEXT NOT NULL,

    nombre            TEXT,
    tipo              tipo_establecimiento NOT NULL,
    -- Subtipo tal cual viene de la fuente (hotel, hostel, apartment, guest_house...)
    subtipo           TEXT,

    lat               DOUBLE PRECISION,
    lon               DOUBLE PRECISION,
    geom              geometry(Point, 4326),

    ccaa              TEXT,
    provincia         TEXT,
    municipio         TEXT,

    rating            NUMERIC(3,2),
    num_reviews       INTEGER,

    fecha_extraccion  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT establecimientos_rating_valido
        CHECK (rating IS NULL OR (rating >= 0 AND rating <= 5)),
    CONSTRAINT establecimientos_num_reviews_valido
        CHECK (num_reviews IS NULL OR num_reviews >= 0),
    CONSTRAINT establecimientos_fuente_unica
        UNIQUE (fuente_dato, id_fuente)
);

-- Índice espacial: imprescindible para los cálculos de densidad de oferta por zona.
CREATE INDEX IF NOT EXISTS idx_establecimientos_geom
    ON establecimientos_turisticos USING GIST (geom);

-- Índices de apoyo para las agregaciones territoriales del dashboard.
CREATE INDEX IF NOT EXISTS idx_establecimientos_ccaa      ON establecimientos_turisticos (ccaa);
CREATE INDEX IF NOT EXISTS idx_establecimientos_municipio ON establecimientos_turisticos (municipio);
CREATE INDEX IF NOT EXISTS idx_establecimientos_tipo      ON establecimientos_turisticos (tipo);

-- Mantiene geom sincronizada con lat/lon: el ETL puede insertar sólo las coordenadas.
CREATE OR REPLACE FUNCTION set_geom_from_latlon()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_geom ON establecimientos_turisticos;
CREATE TRIGGER trg_set_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON establecimientos_turisticos
    FOR EACH ROW EXECUTE FUNCTION set_geom_from_latlon();
