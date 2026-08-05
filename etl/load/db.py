"""
Conexión a la base de datos PostgreSQL/PostGIS alojada en Railway.

Módulo compartido por todos los scripts de carga. Lee DATABASE_URL del fichero .env
de la raíz del proyecto.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


def get_database_url() -> str:
    """Devuelve DATABASE_URL normalizada para SQLAlchemy."""
    load_dotenv(ENV_PATH)

    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            f"DATABASE_URL no está definida.\n"
            f"Copia .env.example a .env y pega la cadena de conexión de Railway.\n"
            f"Fichero esperado: {ENV_PATH}"
        )

    # Railway entrega la cadena como postgresql:// (y a veces postgres://, la forma
    # antigua que SQLAlchemy ya no acepta). Fijamos el driver explícitamente.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


def get_engine(echo: bool = False) -> Engine:
    """Crea el Engine de SQLAlchemy contra la base de datos de Railway."""
    return create_engine(
        get_database_url(),
        echo=echo,
        pool_pre_ping=True,  # Railway cierra conexiones inactivas
        connect_args={"connect_timeout": 30},
    )


def describe_target() -> str:
    """Resumen host/base de datos para los logs, sin exponer la contraseña."""
    from sqlalchemy.engine.url import make_url

    url = make_url(get_database_url())
    return f"{url.host}:{url.port}/{url.database} (usuario: {url.username})"


def test_connection() -> bool:
    """Comprueba que la conexión funciona. Devuelve True si responde."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


if __name__ == "__main__":
    print(f"Conectando a {describe_target()}...")
    test_connection()
    print("Conexión correcta.")
