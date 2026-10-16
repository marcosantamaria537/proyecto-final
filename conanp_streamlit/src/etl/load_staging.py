
# src/etl/load_staging.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine


# ==========================================================
# 1. TABLAS PERMITIDAS
# ==========================================================

STAGING_TABLES = {
    "faltas": "faltas_raw",
    "sin_faltas": "sin_faltas_raw",
    "recorridos": "recorridos_raw",
}

STAGING_SCHEMA = "staging"


# ==========================================================
# 2. RESULTADO DE VALIDACIÓN
# ==========================================================

@dataclass
class StagingValidation:
    tipo: str
    tabla: str
    registros_entrada: int
    registros_existentes: int
    columnas_entrada: list[str]
    columnas_destino: list[str]
    columnas_desconocidas: list[str]
    columnas_faltantes: list[str]
    errores: list[str]
    compatible: bool


# ==========================================================
# 3. CREAR CONEXIÓN
# ==========================================================

def create_db_engine(
    connection_string: str
) -> Engine:

    if not connection_string:
        raise ValueError(
            "No se proporcionó la conexión PostgreSQL."
        )

    if not connection_string.startswith(
        ("postgresql://", "postgresql+psycopg://",
         "postgresql+psycopg2://")
    ):
        raise ValueError(
            "La conexión debe utilizar PostgreSQL."
        )

    return create_engine(
        connection_string,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


# ==========================================================
# 4. INSPECCIONAR UNA TABLA
# ==========================================================

def inspect_staging_table(
    engine: Engine,
    tipo: str
) -> dict[str, Any]:

    if tipo not in STAGING_TABLES:
        raise ValueError(
            f"Tipo de fuente desconocido: {tipo}"
        )

    tabla = STAGING_TABLES[tipo]

    inspector = inspect(engine)

    if not inspector.has_table(
        tabla,
        schema=STAGING_SCHEMA
    ):
        raise ValueError(
            f"No existe staging.{tabla}"
        )

    columns = inspector.get_columns(
        tabla,
        schema=STAGING_SCHEMA
    )

    primary_key = inspector.get_pk_constraint(
        tabla,
        schema=STAGING_SCHEMA
    )

    unique_constraints = (
        inspector.get_unique_constraints(
            tabla,
            schema=STAGING_SCHEMA
        )
    )

    with engine.connect() as connection:
        # Nombre de tabla procedente de lista fija.
        total = connection.execute(
            text(
                f"SELECT COUNT(*) "
                f"FROM staging.{tabla}"
            )
        ).scalar_one()

    return {
        "tipo": tipo,
        "tabla": f"staging.{tabla}",
        "registros_existentes": total,
        "columnas": columns,
        "clave_primaria": primary_key,
        "restricciones_unicas": unique_constraints,
    }


# ==========================================================
# 5. VALIDAR COMPATIBILIDAD
# ==========================================================

def validate_staging_dataframe(
    engine: Engine,
    tipo: str,
    df: pd.DataFrame
) -> StagingValidation:

    info = inspect_staging_table(engine, tipo)

    columns = info["columnas"]

    destination = [
        column["name"] for column in columns
    ]

    source = list(df.columns)

    unknown = [
        col for col in source
        if col not in destination
    ]

    missing = [
        col for col in destination
        if col not in source
    ]

    errors = []

    if df.empty:
        errors.append(
            "El DataFrame no contiene registros."
        )

    if df.columns.duplicated().any():
        errors.append(
            "Existen nombres de columnas repetidos."
        )

    if unknown:
        errors.append(
            "Hay columnas que no existen "
            "en la tabla destino."
        )

    # Verificar columnas obligatorias.
    for column in columns:

        name = column["name"]

        required = (
            not column["nullable"]
            and column.get("default") is None
            and column.get("identity") is None
            and not column.get("autoincrement")
            and column.get("computed") is None
        )

        if required and name not in source:
            errors.append(
                f"Falta columna obligatoria: {name}"
            )

        elif required and df[name].isna().any():
            errors.append(
                f"Hay nulos en columna obligatoria: {name}"
            )

    return StagingValidation(
        tipo=tipo,
        tabla=info["tabla"],
        registros_entrada=len(df),
        registros_existentes=info[
            "registros_existentes"
        ],
        columnas_entrada=source,
        columnas_destino=destination,
        columnas_desconocidas=unknown,
        columnas_faltantes=missing,
        errores=errors,
        compatible=len(errors) == 0,
    )


# ==========================================================
# 6. CARGA PROTEGIDA
# ==========================================================

def load_staging(
    engine: Engine,
    tipo: str,
    df: pd.DataFrame,
    confirm_write: bool = False
) -> dict:

    """
    Valida y opcionalmente inserta registros.

    Seguridad:
    - La escritura está desactivada por defecto.
    - No reemplaza tablas.
    - Bloquea inserciones en tablas no vacías.
    - Utiliza una transacción.

    El control definitivo de duplicados se
    implementará con las tablas etl.*
    """

    validation = validate_staging_dataframe(
        engine,
        tipo,
        df
    )

    report = {
        "tipo": tipo,
        "tabla": validation.tabla,
        "registros_entrada": len(df),
        "registros_insertados": 0,
        "compatible": validation.compatible,
        "errores": validation.errores,
        "estado": "SOLO_VALIDACION",
    }

    if not validation.compatible:
        report["estado"] = "INCOMPATIBLE"
        return report

    if not confirm_write:
        return report

    tabla = STAGING_TABLES[tipo]

    # Validaciones realizadas nuevamente bajo el
    # bloqueo de la tabla para impedir otra inserción
    # concurrente mediante este mismo proceso.
    with engine.begin() as connection:

        connection.execute(
            text(
                f"LOCK TABLE staging.{tabla} "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
        )

        current_count = connection.execute(
            text(
                f"SELECT COUNT(*) FROM staging.{tabla}"
            )
        ).scalar_one()

        if current_count > 0:
            raise RuntimeError(
                f"staging.{tabla} ya contiene "
                f"{current_count} registros. "
                "La inserción está bloqueada "
                "hasta implementar deduplicación."
            )

        # Inserción solo cuando la tabla está vacía.
        df.to_sql(
            name=tabla,
            con=connection,
            schema=STAGING_SCHEMA,
            if_exists="append",
            index=False,
            chunksize=500,
        )

    report["registros_insertados"] = len(df)
    report["estado"] = "INSERTADO"

    return report
