
# src/etl/common.py

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time

import pandas as pd


# ==========================================================
# 1. NORMALIZACIÓN DE TEXTOS
# ==========================================================

def normalize_text(value):
    """
    Elimina espacios innecesarios y normaliza valores vacíos.
    Conserva acentos y mayúsculas originales.
    """
    if value is None:
        return None

    if pd.isna(value):
        return None

    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)

    if text.lower() in ("", "nan", "none", "null"):
        return None

    return text


def normalize_key(value):
    """
    Convierte un texto a una clave uniforme para comparaciones.
    Ejemplo:
        'Polígono 1' -> 'POLIGONO 1'
    """
    text = normalize_text(value)

    if text is None:
        return None

    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )

    return text.upper()


# ==========================================================
# 2. NORMALIZACIÓN DE ENCABEZADOS
# ==========================================================

def normalize_column_name(value):
    """
    Convierte encabezados Excel al formato snake_case.
    Ejemplo:
        'Fecha de Supervisión' -> 'fecha_de_supervision'
    """
    text = normalize_key(value)

    if text is None:
        raise ValueError("Se encontró un encabezado vacío.")

    text = re.sub(r"[^A-Z0-9]+", "_", text)
    text = text.strip("_").lower()

    if not text:
        raise ValueError("Se encontró un encabezado inválido.")

    return text


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza los encabezados y detecta colisiones
    entre nombres de columnas.
    """
    df = df.copy()

    new_columns = [
        normalize_column_name(col)
        for col in df.columns
    ]

    duplicated = pd.Index(new_columns).duplicated()

    if duplicated.any():
        repeated = pd.Index(new_columns)[duplicated].tolist()
        raise ValueError(
            f"Columnas duplicadas después de normalizar: {repeated}"
        )

    df.columns = new_columns

    return df


# ==========================================================
# 3. CONVERSIÓN DE FECHAS
# ==========================================================

def parse_date(value):
    """
    Convierte fechas Excel y textos a objetos date.

    Para fechas en texto se utiliza formato de día primero.
    Los valores inválidos se convierten en None.
    """
    if value is None or pd.isna(value):
        return None

    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value

    try:
        parsed = pd.to_datetime(
            value,
            errors="coerce",
            dayfirst=True
        )

        if pd.isna(parsed):
            return None

        return parsed.date()

    except (ValueError, TypeError, OverflowError):
        return None


# ==========================================================
# 4. CONVERSIÓN DE HORAS
# ==========================================================

def parse_time(value):
    """
    Convierte horas válidas a objetos time.
    """
    if value is None or pd.isna(value):
        return None

    if isinstance(value, datetime):
        return value.time()

    if isinstance(value, time):
        return value

    try:
        parsed = pd.to_datetime(
            str(value).strip(),
            format="%H:%M:%S",
            errors="coerce"
        )

        if pd.isna(parsed):
            parsed = pd.to_datetime(
                str(value).strip(),
                format="%H:%M",
                errors="coerce"
            )

        if pd.isna(parsed):
            return None

        return parsed.time()

    except (ValueError, TypeError):
        return None


# ==========================================================
# 5. CONVERSIÓN NUMÉRICA
# ==========================================================

def parse_int(value):
    """
    Convierte valores a enteros cuando son válidos.
    No redondea números decimales.
    """
    if value is None or pd.isna(value):
        return None

    try:
        number = float(value)

        if not number.is_integer():
            return None

        return int(number)

    except (ValueError, TypeError, OverflowError):
        return None


# ==========================================================
# 6. IDENTIFICACIÓN DE CAMPOS OBLIGATORIOS
# ==========================================================

def validate_required_columns(df, required_columns):
    """
    Comprueba la existencia de las columnas obligatorias.
    """
    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Faltan columnas obligatorias: {missing}"
        )

    return True


def validate_required_values(df, required_columns):
    """
    Identifica filas con campos obligatorios vacíos.

    Devuelve:
        valid_df, rejected_df
    """
    validate_required_columns(df, required_columns)

    mask = pd.Series(True, index=df.index)

    for column in required_columns:
        values = df[column].apply(normalize_text)
        mask &= values.notna()

    valid_df = df.loc[mask].copy()
    rejected_df = df.loc[~mask].copy()

    return valid_df, rejected_df


# ==========================================================
# 7. DETECCIÓN DE DUPLICADOS
# ==========================================================

def detect_duplicates(df, subset_columns):
    """
    Marca duplicados sin eliminar registros.
    La clave de negocio debe definirse por cada ETL.
    """
    validate_required_columns(df, subset_columns)

    result = df.copy()

    result["es_duplicado"] = result.duplicated(
        subset=subset_columns,
        keep="first"
    )

    return result


# ==========================================================
# 8. SEPARACIÓN DE VALORES MÚLTIPLES
# ==========================================================

def split_multi_value(value, separator=","):
    """
    Divide valores múltiples en una lista.

    Ejemplo:
        'ANCLAJE, VELOCIDAD'
        -> ['ANCLAJE', 'VELOCIDAD']
    """
    text = normalize_text(value)

    if text is None:
        return []

    return [
        item
        for part in text.split(separator)
        if (item := normalize_text(part)) is not None
    ]


# ==========================================================
# 9. NORMALIZACIÓN DE UBICACIONES
# ==========================================================

def normalize_location(value, equivalences=None):
    """
    Normaliza una ubicación con un catálogo autorizado.

    Si no existe una equivalencia, conserva el valor
    normalizado en lugar de asignar un polígono arbitrario.
    """
    key = normalize_key(value)

    if key is None:
        return None

    if equivalences is None:
        return key

    normalized_equivalences = {
        normalize_key(source): destination
        for source, destination in equivalences.items()
    }

    return normalized_equivalences.get(key, key)


# ==========================================================
# 10. RESUMEN DE CALIDAD
# ==========================================================

def quality_summary(df):
    """
    Genera indicadores básicos sobre la calidad de los datos.
    """
    total = len(df)

    missing_by_column = df.isna().sum().to_dict()

    return {
        "total_registros": total,
        "total_columnas": len(df.columns),
        "filas_duplicadas_exactas": int(
            df.duplicated().sum()
        ),
        "valores_nulos_por_columna": {
            str(key): int(value)
            for key, value in missing_by_column.items()
        }
    }
