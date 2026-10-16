
# src/etl/recorridos.py

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .common import (
    normalize_columns,
    normalize_text,
    normalize_key,
    normalize_location,
    parse_date,
    parse_time,
    detect_duplicates,
    validate_required_columns,
    quality_summary,
)


# ==========================================================
# 1. CONFIGURACIÓN DE COLUMNAS
# ==========================================================

# Estos alias son provisionales.
# Deben ajustarse a los encabezados reales del Excel.
COLUMN_ALIASES = {
    "fecha": [
        "fecha",
        "fecha_recorrido",
        "fecha_de_recorrido",
    ],
    "ubicacion": [
        "ubicacion",
        "poligono",
        "lugar_del_recorrido",
        "zona",
    ],
    "actividad_realizada": [
        "actividad_realizada",
        "actividad",
        "tipo_actividad",
    ],
    "embarcacion": [
        "embarcacion",
        "nombre_embarcacion",
    ],
    "hora_inicio": [
        "hora_inicio",
        "hora_de_inicio",
        "inicio_recorrido",
    ],
    "hora_fin": [
        "hora_fin",
        "hora_de_termino",
        "hora_termino",
        "fin_recorrido",
    ],
}

# Campos obligatorios preliminares.
REQUIRED_COLUMNS = ["fecha"]

# No asignar equivalencias sin confirmarlas.
LOCATION_EQUIVALENCES = {}


# ==========================================================
# 2. ESTRUCTURA DEL RESULTADO
# ==========================================================

@dataclass
class RecorridosResult:
    validos: pd.DataFrame
    rechazados: pd.DataFrame
    duplicados: pd.DataFrame
    incidencias: pd.DataFrame
    resumen: dict


# ==========================================================
# 3. ESTANDARIZACIÓN DE COLUMNAS
# ==========================================================

def standardize_columns(
    df: pd.DataFrame
) -> pd.DataFrame:

    result = normalize_columns(df)

    for canonical, aliases in COLUMN_ALIASES.items():

        found = [
            name for name in aliases
            if name in result.columns
        ]

        if len(found) > 1:
            raise ValueError(
                f"Varias columnas representan '{canonical}': "
                f"{found}"
            )

        if found:
            source = found[0]

            if source != canonical:
                result = result.rename(
                    columns={source: canonical}
                )

    return result


# ==========================================================
# 4. LIMPIEZA Y TRANSFORMACIÓN
# ==========================================================

def clean_recorridos(
    df: pd.DataFrame
) -> pd.DataFrame:

    result = standardize_columns(df)

    validate_required_columns(
        result,
        REQUIRED_COLUMNS
    )

    if "_fila_excel" not in result.columns:
        raise ValueError(
            "Falta '_fila_excel'. "
            "Utilice extract.py para leer el archivo."
        )

    if result["_fila_excel"].isna().any():
        raise ValueError(
            "Existen registros sin número de fila."
        )

    if result["_fila_excel"].duplicated().any():
        raise ValueError(
            "Existen números de fila de origen repetidos."
        )

    # Conservar originales para auditoría.
    result["_fecha_original"] = result["fecha"]

    if "ubicacion" in result.columns:
        result["_ubicacion_original"] = result["ubicacion"]

    # Limpieza de textos.
    for column in result.columns:

        if column.startswith("_"):
            continue

        if column in [
            "fecha",
            "hora_inicio",
            "hora_fin",
        ]:
            continue

        result[column] = result[column].map(
            lambda value: (
                normalize_text(value)
                if isinstance(value, str)
                else value
            )
        )

    # Normalización de fechas.
    result["fecha"] = result["fecha"].map(
        parse_date
    )

    # Conversión de horarios.
    for column in ["hora_inicio", "hora_fin"]:
        if column in result.columns:
            result[f"_{column}_original"] = result[column]
            result[column] = result[column].map(
                parse_time
            )

    # Normalización de ubicaciones.
    if "ubicacion" in result.columns:
        result["ubicacion_normalizada"] = (
            result["ubicacion"].map(
                lambda value: normalize_location(
                    value,
                    LOCATION_EQUIVALENCES
                )
            )
        )

    # Normalización de actividades.
    if "actividad_realizada" in result.columns:
        result["actividad_normalizada"] = (
            result["actividad_realizada"].map(
                normalize_key
            )
        )

    result["_tipo_registro"] = "RECORRIDOS"

    return result


# ==========================================================
# 5. VALIDACIÓN DE REGISTROS
# ==========================================================

def validate_recorridos(
    df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    result = df.copy()
    incidencias = []

    result["_motivos_rechazo"] = ""

    def registrar_error(index, campo, mensaje):

        actual = result.at[index, "_motivos_rechazo"]

        result.at[index, "_motivos_rechazo"] = (
            f"{actual}; {mensaje}".strip("; ")
        )

        incidencias.append({
            "fila_excel": result.at[index, "_fila_excel"],
            "campo": campo,
            "tipo": "ERROR",
            "descripcion": mensaje,
        })

    # Validación de fecha.
    for index in result.index[
        result["fecha"].isna()
    ]:
        registrar_error(
            index,
            "fecha",
            "Fecha obligatoria ausente o inválida"
        )

    # Validación de horarios.
    for column in ["hora_inicio", "hora_fin"]:

        if column not in result.columns:
            continue

        original_column = f"_{column}_original"

        invalid = (
            result[original_column].notna()
            & result[column].isna()
        )

        for index in result.index[invalid]:
            registrar_error(
                index,
                column,
                f"Horario inválido en {column}"
            )

    # Verificación de secuencia de horarios.
    # No se rechazan automáticamente los recorridos
    # nocturnos porque podrían cruzar la medianoche.
    if {
        "hora_inicio", "hora_fin"
    }.issubset(result.columns):

        valid_times = (
            result["hora_inicio"].notna()
            & result["hora_fin"].notna()
        )

        for index in result.index[valid_times]:

            inicio = result.at[index, "hora_inicio"]
            fin = result.at[index, "hora_fin"]

            if fin < inicio:
                incidencias.append({
                    "fila_excel": result.at[index, "_fila_excel"],
                    "campo": "hora_fin",
                    "tipo": "ADVERTENCIA",
                    "descripcion": (
                        "La hora de término es anterior "
                        "a la hora de inicio. Verificar "
                        "si el recorrido cruzó medianoche."
                    ),
                })

    # Validar posteriormente ubicaciones y actividades
    # contra los catálogos del almacén dimensional.
    if "ubicacion_normalizada" in result.columns:

        for index in result.index[
            result["ubicacion_normalizada"].notna()
        ]:
            incidencias.append({
                "fila_excel": result.at[index, "_fila_excel"],
                "campo": "ubicacion_normalizada",
                "tipo": "PENDIENTE_CATALOGO",
                "descripcion": (
                    "Verificar correspondencia con "
                    "dw.dim_ubicacion"
                ),
            })

    mask_valid = result["_motivos_rechazo"].eq("")

    validos = result.loc[mask_valid].copy()
    rechazados = result.loc[~mask_valid].copy()

    incidencias_df = pd.DataFrame(
        incidencias,
        columns=[
            "fila_excel",
            "campo",
            "tipo",
            "descripcion",
        ]
    )

    return validos, rechazados, incidencias_df


# ==========================================================
# 6. DETECCIÓN DE POSIBLES DUPLICADOS
# ==========================================================

def mark_possible_duplicates(
    df: pd.DataFrame
) -> pd.DataFrame:

    result = df.copy()
    result["es_duplicado"] = False

    # Clave preliminar.
    # Debe validarse con los datos históricos.
    key_columns = [
        "fecha",
        "ubicacion_normalizada",
        "hora_inicio",
        "hora_fin",
    ]

    if not all(
        column in result.columns
        for column in key_columns
    ):
        return result

    # No marcar coincidencias cuando faltan
    # componentes de la clave propuesta.
    complete = result[key_columns].notna().all(axis=1)

    if complete.any():

        checked = detect_duplicates(
            result.loc[complete],
            key_columns
        )

        result.loc[
            checked.index,
            "es_duplicado"
        ] = checked["es_duplicado"]

    return result


# ==========================================================
# 7. EJECUCIÓN DEL ETL DE RECORRIDOS
# ==========================================================

def transform_recorridos(
    df: pd.DataFrame
) -> RecorridosResult:

    if df.empty:
        raise ValueError(
            "El conjunto de recorridos está vacío."
        )

    cleaned = clean_recorridos(df)

    validos, rechazados, incidencias = (
        validate_recorridos(cleaned)
    )

    validos = mark_possible_duplicates(validos)

    duplicados = validos.loc[
        validos["es_duplicado"]
    ].copy()

    resumen = {
        "registros_originales": len(df),
        "registros_validos": len(validos),
        "registros_rechazados": len(rechazados),
        "posibles_duplicados": len(duplicados),
        "calidad": quality_summary(validos),
    }

    return RecorridosResult(
        validos=validos,
        rechazados=rechazados,
        duplicados=duplicados,
        incidencias=incidencias,
        resumen=resumen,
    )
