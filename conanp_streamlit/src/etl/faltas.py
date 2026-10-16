
# src/etl/faltas.py

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

# Son nombres de ejemplo.
# Deben verificarse contra los encabezados reales del Excel.
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
    "tipo_falta": [
        "tipo_falta",
        "tipo_de_falta",
        "falta",
        "faltas",
    ],
    "actividad_realizada": [
        "actividad_realizada",
        "actividad",
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

# Configuración preliminar.
# Confirmar con los expertos cuáles son realmente obligatorios.
REQUIRED_COLUMNS = [
    "fecha",
    "ubicacion",
    "tipo_falta",
]

# Estas equivalencias deberán validarse con CONANP.
# No asignar automáticamente ubicaciones ambiguas.
LOCATION_EQUIVALENCES = {}


# ==========================================================
# 2. RESULTADO DEL PROCESO
# ==========================================================

@dataclass
class FaltasResult:
    validos: pd.DataFrame
    rechazados: pd.DataFrame
    duplicados: pd.DataFrame
    incidencias: pd.DataFrame
    relaciones_falta: pd.DataFrame
    resumen: dict


# ==========================================================
# 3. ESTANDARIZAR ENCABEZADOS
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
                f"Existen varias columnas que representan "
                f"'{canonical}': {found}. "
                "Revise los encabezados."
            )

        if found:
            source = found[0]

            if source != canonical:
                result = result.rename(
                    columns={source: canonical}
                )

    return result


# ==========================================================
# 4. LIMPIAR LOS REGISTROS
# ==========================================================

def clean_faltas(df: pd.DataFrame) -> pd.DataFrame:
    result = standardize_columns(df)

    validate_required_columns(
        result,
        REQUIRED_COLUMNS
    )

    # Trazabilidad: extract.py ya proporciona este valor.
    if "_fila_excel" not in result.columns:
        raise ValueError(
            "Falta '_fila_excel'. "
            "Utilice extract.py para leer el archivo."
        )

    if result["_fila_excel"].isna().any():
        raise ValueError(
            "Existen registros sin número de fila de origen."
        )

    if result["_fila_excel"].duplicated().any():
        raise ValueError(
            "Los números de fila de origen están repetidos."
        )

    # Conservar originales para auditoría.
    result["_fecha_original"] = result["fecha"]
    result["_ubicacion_original"] = result["ubicacion"]
    result["_tipo_falta_original"] = result["tipo_falta"]

    # Limpiar textos sin eliminar otras columnas.
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

    result["fecha"] = result["fecha"].map(parse_date)

    if "hora" in result.columns:
        result["hora"] = result["hora"].map(parse_time)

    result["ubicacion_normalizada"] = (
        result["ubicacion"].map(
            lambda value: normalize_location(
                value,
                LOCATION_EQUIVALENCES
            )
        )
    )

    result["tipo_falta_normalizado"] = (
        result["tipo_falta"].map(normalize_key)
    )

    result["_tipo_registro"] = "FALTAS"

    return result


# ==========================================================
# 5. VALIDAR REGISTROS
# ==========================================================

def validate_faltas(
    df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    result = df.copy()
    incidencias = []

    required = {
        "fecha": "Fecha obligatoria ausente o inválida",
        "ubicacion_normalizada": "Ubicación obligatoria vacía",
        "tipo_falta_normalizado": "Tipo de falta obligatorio vacío",
    }

    result["_motivos_rechazo"] = ""

    for column, message in required.items():
        mask = result[column].isna()

        for index in result.index[mask]:
            current = result.at[index, "_motivos_rechazo"]

            result.at[index, "_motivos_rechazo"] = (
                f"{current}; {message}".strip("; ")
            )

            incidencias.append({
                "fila_excel": result.at[index, "_fila_excel"],
                "campo": column,
                "tipo": "ERROR",
                "descripcion": message,
            })

    # Una ubicación normalizada no implica que
    # ya esté asociada a una dimensión de PostgreSQL.
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

    return (
        validos,
        rechazados,
        pd.DataFrame(
            incidencias,
            columns=[
                "fila_excel",
                "campo",
                "tipo",
                "descripcion",
            ]
        ),
    )


# ==========================================================
# 6. DETECTAR POSIBLES DUPLICADOS
# ==========================================================

def mark_possible_duplicates(
    df: pd.DataFrame
) -> pd.DataFrame:

    result = df.copy()

    # Criterio provisional: no usarlo aún
    # para eliminar o fusionar registros.
    key_columns = [
        "fecha",
        "ubicacion_normalizada",
        "embarcacion",
        "hora",
    ]

    available = [
        column for column in key_columns
        if column in result.columns
    ]

    # No considerar suficiente una clave incompleta.
    if set(available) != set(key_columns):
        result["es_duplicado"] = False
        return result

    result["es_duplicado"] = False

    complete = result[available].notna().all(axis=1)

    if complete.any():
        checked = detect_duplicates(
            result.loc[complete],
            available
        )

        result.loc[
            checked.index, "es_duplicado"
        ] = checked["es_duplicado"]

    return result


# ==========================================================
# 7. PREPARAR RELACIONES CON TIPOS DE FALTA
# ==========================================================

def prepare_fault_relations(
    df: pd.DataFrame
) -> pd.DataFrame:

    rows = []

    for _, record in df.iterrows():

        fault = record["tipo_falta_normalizado"]

        if fault is None:
            continue

        rows.append({
            "fila_excel": record["_fila_excel"],
            "tipo_falta_texto": fault,
            "cantidad": 1,
        })

    return pd.DataFrame(
        rows,
        columns=[
            "fila_excel",
            "tipo_falta_texto",
            "cantidad",
        ]
    )


# ==========================================================
# 8. PIPELINE DE TRANSFORMACIÓN DE FALTAS
# ==========================================================

def transform_faltas(
    df: pd.DataFrame
) -> FaltasResult:

    if df.empty:
        raise ValueError(
            "El conjunto de faltas está vacío."
        )

    cleaned = clean_faltas(df)

    validos, rechazados, incidencias = (
        validate_faltas(cleaned)
    )

    validos = mark_possible_duplicates(validos)

    duplicados = validos.loc[
        validos["es_duplicado"]
    ].copy()

    relaciones = prepare_fault_relations(validos)

    resumen = {
        "registros_originales": len(df),
        "registros_validos": len(validos),
        "registros_rechazados": len(rechazados),
        "posibles_duplicados": len(duplicados),
        "relaciones_preparadas": len(relaciones),
        "calidad": quality_summary(validos),
    }

    return FaltasResult(
        validos=validos,
        rechazados=rechazados,
        duplicados=duplicados,
        incidencias=incidencias,
        relaciones_falta=relaciones,
        resumen=resumen,
    )
