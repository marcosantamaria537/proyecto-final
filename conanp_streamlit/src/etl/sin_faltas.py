
# src/etl/sin_faltas.py

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

# Estos alias se ajustarán al revisar el Excel original.
COLUMN_ALIASES = {
    "fecha": [
        "fecha",
        "fecha_supervision",
        "fecha_de_supervision",
    ],
    "ubicacion": [
        "ubicacion",
        "poligono",
        "lugar_de_supervision",
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
    "hora": [
        "hora",
        "hora_supervision",
        "hora_de_supervision",
    ],
}

# Reglas preliminares: confirmar campos obligatorios
# con el formato real de CONANP.
REQUIRED_COLUMNS = [
    "fecha",
    "ubicacion",
]

# Debe completarse con equivalencias autorizadas.
LOCATION_EQUIVALENCES = {}


# ==========================================================
# 2. ESTRUCTURA DEL RESULTADO
# ==========================================================

@dataclass
class SinFaltasResult:
    validos: pd.DataFrame
    rechazados: pd.DataFrame
    duplicados: pd.DataFrame
    incidencias: pd.DataFrame
    resumen: dict


# ==========================================================
# 3. ESTANDARIZACIÓN DE COLUMNAS
# ==========================================================

def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:

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

def clean_sin_faltas(
    df: pd.DataFrame
) -> pd.DataFrame:

    result = standardize_columns(df)

    validate_required_columns(
        result,
        REQUIRED_COLUMNS
    )

    # El extractor debe proporcionar el número de fila.
    if "_fila_excel" not in result.columns:
        raise ValueError(
            "Falta '_fila_excel'. Utilice extract.py."
        )

    if result["_fila_excel"].isna().any():
        raise ValueError(
            "Existen filas sin número de origen."
        )

    if result["_fila_excel"].duplicated().any():
        raise ValueError(
            "Existen números de fila de origen repetidos."
        )

    # Conservar valores originales para auditoría.
    result["_fecha_original"] = result["fecha"]
    result["_ubicacion_original"] = result["ubicacion"]

    # Limpieza de textos
    for column in result.columns:

        if column.startswith("_"):
            continue

        if column in ["fecha", "hora"]:
            continue

        result[column] = result[column].map(
            lambda value: (
                normalize_text(value)
                if isinstance(value, str)
                else value
            )
        )

    # Conversión de fechas
    result["fecha"] = result["fecha"].map(
        parse_date
    )

    # Conversión de horas
    if "hora" in result.columns:
        result["hora"] = result["hora"].map(
            parse_time
        )

    # Normalización de ubicación
    result["ubicacion_normalizada"] = (
        result["ubicacion"].map(
            lambda value: normalize_location(
                value,
                LOCATION_EQUIVALENCES
            )
        )
    )

    # Actividad normalizada
    if "actividad_realizada" in result.columns:
        result["actividad_normalizada"] = (
            result["actividad_realizada"].map(
                normalize_key
            )
        )

    result["_tipo_registro"] = "SIN_FALTAS"

    return result


# ==========================================================
# 5. VALIDACIÓN DE REGISTROS
# ==========================================================

def validate_sin_faltas(
    df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    result = df.copy()

    incidencias = []

    required = {
        "fecha": "Fecha obligatoria ausente o inválida",
        "ubicacion_normalizada": "Ubicación obligatoria vacía",
    }

    result["_motivos_rechazo"] = ""

    for column, message in required.items():

        mask = result[column].isna()

        for index in result.index[mask]:

            current = result.at[
                index,
                "_motivos_rechazo"
            ]

            result.at[
                index,
                "_motivos_rechazo"
            ] = f"{current}; {message}".strip("; ")

            incidencias.append({
                "fila_excel": result.at[
                    index, "_fila_excel"
                ],
                "campo": column,
                "tipo": "ERROR",
                "descripcion": message,
            })

    # El mapeo definitivo de ubicaciones se
    # realizará contra dw.dim_ubicacion.
    for index in result.index[
        result["ubicacion_normalizada"].notna()
    ]:
        incidencias.append({
            "fila_excel": result.at[
                index, "_fila_excel"
            ],
            "campo": "ubicacion_normalizada",
            "tipo": "PENDIENTE_CATALOGO",
            "descripcion": (
                "Validar correspondencia con "
                "dw.dim_ubicacion"
            ),
        })

    mask_valid = result[
        "_motivos_rechazo"
    ].eq("")

    validos = result.loc[
        mask_valid
    ].copy()

    rechazados = result.loc[
        ~mask_valid
    ].copy()

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
# 6. POSIBLES DUPLICADOS
# ==========================================================

def mark_possible_duplicates(
    df: pd.DataFrame
) -> pd.DataFrame:

    result = df.copy()

    # Clave provisional para buscar coincidencias.
    # Debe confirmarse antes de descartar registros.
    key_columns = [
        "fecha",
        "ubicacion_normalizada",
        "embarcacion",
        "hora",
    ]

    result["es_duplicado"] = False

    if not all(
        column in result.columns
        for column in key_columns
    ):
        return result

    complete = result[
        key_columns
    ].notna().all(axis=1)

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
# 7. EJECUTAR ETL DE SIN FALTAS
# ==========================================================

def transform_sin_faltas(
    df: pd.DataFrame
) -> SinFaltasResult:

    if df.empty:
        raise ValueError(
            "El conjunto SIN FALTAS está vacío."
        )

    cleaned = clean_sin_faltas(df)

    validos, rechazados, incidencias = (
        validate_sin_faltas(cleaned)
    )

    validos = mark_possible_duplicates(
        validos
    )

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

    return SinFaltasResult(
        validos=validos,
        rechazados=rechazados,
        duplicados=duplicados,
        incidencias=incidencias,
        resumen=resumen,
    )
