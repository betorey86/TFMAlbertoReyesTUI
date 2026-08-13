"""
Inicializa la base de datos de Railway: habilita PostGIS y aplica db/schema.sql.

Crea el modelo de dos niveles del proyecto: `municipios` (unidad de análisis),
`establecimientos` (detalle con punto) y `agregados_municipales` (comparación entre
territorios), más la vista `indicadores_municipales`.

Es idempotente: se puede volver a lanzar tras tocar el esquema. Las tablas se crean con
IF NOT EXISTS, así que **no** recrea ni borra lo ya existente; si cambias una columna de
una tabla ya creada, hay que migrarla a mano o borrarla antes.

Uso:
    python etl/load/init_db.py
    python etl/load/init_db.py --solo-comprobar    # sólo verifica conexión y estado
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permite ejecutar el script directamente (python etl/load/init_db.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db import PROJECT_ROOT, describe_target, get_engine

SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"

AYUDA_POSTGIS = """
No se pudo crear la extensión PostGIS en esta base de datos.

El servicio "Postgres" por defecto de Railway NO incluye PostGIS. Necesitas desplegar
una imagen que sí lo traiga:

  Railway > New > Database > "Add PostgreSQL"  ->  NO sirve para este proyecto
  Railway > New > Docker Image > postgis/postgis:16-3.4  ->  sí

Al desplegar postgis/postgis por imagen tendrás que definir a mano las variables
POSTGRES_USER, POSTGRES_PASSWORD y POSTGRES_DB en el servicio, y luego construir la
DATABASE_URL con el host y puerto públicos que Railway te asigne.

Alternativa: Railway ofrece plantillas de la comunidad; busca "PostGIS" en
railway.app/templates.
"""


def comprobar_estado(engine) -> None:
    """Informa de la versión de PostGIS y de las tablas ya presentes."""
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
        print(f"  Servidor: {version.split(',')[0]}")

        postgis = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
        ).scalar()
        print(f"  PostGIS: {postgis if postgis else 'NO instalado'}")

        tablas = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        ).scalars().all()
        print(f"  Tablas en public: {', '.join(tablas) if tablas else '(ninguna)'}")

        vistas = conn.execute(
            text("SELECT viewname FROM pg_views WHERE schemaname = 'public' ORDER BY viewname")
        ).scalars().all()
        if vistas:
            print(f"  Vistas en public:  {', '.join(vistas)}")

        for tabla in ("municipios", "establecimientos", "agregados_municipales"):
            if tabla in tablas:
                n = conn.execute(text(f"SELECT COUNT(*) FROM {tabla}")).scalar()
                print(f"  Filas en {tabla}: {n:,}".replace(",", "."))


def habilitar_postgis(engine) -> None:
    print("\n[2/3] Habilitando extensión PostGIS...")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
    except SQLAlchemyError as exc:
        print(f"\nERROR al crear la extensión PostGIS:\n  {exc}", file=sys.stderr)
        print(AYUDA_POSTGIS, file=sys.stderr)
        raise SystemExit(3)
    print("  PostGIS disponible.")


def aplicar_schema(engine) -> None:
    print(f"\n[3/3] Aplicando {SCHEMA_PATH.relative_to(PROJECT_ROOT)}...")

    if not SCHEMA_PATH.exists():
        print(f"ERROR: no se encuentra {SCHEMA_PATH}", file=sys.stderr)
        raise SystemExit(4)

    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    # Se envía el fichero entero en una sola llamada: psycopg2 admite varias sentencias
    # por execute, y así no hay que partir por ';' (rompería los bloques DO $$ ... $$
    # y el cuerpo de la función del trigger).
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
    except SQLAlchemyError as exc:
        print(f"\nERROR al aplicar el esquema:\n  {exc}", file=sys.stderr)
        raise SystemExit(5)

    print("  Esquema aplicado correctamente.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Habilita PostGIS y aplica db/schema.sql en la base de datos de Railway."
    )
    parser.add_argument(
        "--solo-comprobar",
        action="store_true",
        help="Sólo comprueba la conexión y el estado actual, sin modificar nada.",
    )
    args = parser.parse_args()

    print("[1/3] Conectando a la base de datos...")
    try:
        destino = describe_target()
        engine = get_engine()
        print(f"  Destino: {destino}")
        comprobar_estado(engine)
    except RuntimeError as exc:  # DATABASE_URL ausente o mal formada
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"\nERROR de conexión: {exc}", file=sys.stderr)
        print(
            "\nRevisa que DATABASE_URL sea la URL PÚBLICA de Railway "
            "(DATABASE_PUBLIC_URL), no la interna (*.railway.internal).",
            file=sys.stderr,
        )
        return 2

    if args.solo_comprobar:
        print("\nModo comprobación: no se ha modificado nada.")
        return 0

    habilitar_postgis(engine)
    aplicar_schema(engine)

    print("\n--- Estado final ---")
    comprobar_estado(engine)
    print("\nBase de datos lista.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
