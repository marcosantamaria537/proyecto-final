
# src/etl/extract.py

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO
import hashlib

import pandas as pd

from .common import normalize_key, normalize_columns


# ==========================================================
# 1. TIPOS DE HOJAS RECONOCIDAS
# ==========================================================

SHEET_ALIASES = {
    "FALTAS": "faltas",
    "SIN FALTAS": "sin_faltas",
    "SIN_FALTAS": "sin_faltas",
    "SIN FALTA": "sin_faltas",
    "RECORRIDOS": "recorridos",
    "RECORRIDO": "recorridos",
}


# ==========================================================
# 2. OBTENER CONTENIDO DEL ARCHIVO
# ==========================================================

def get_file_bytes(
    source: str | Path | bytes | BinaryIO
) -> bytes:
    """
    Obtiene los bytes de un archivo Excel.

    Admite:
    - Ruta local
    - Bytes
    - Archivo cargado desde Streamlit
    """

    if isinstance(source, bytes):
        return source

    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()

    if hasattr(source, "getvalue"):
        return source.getvalue()

    if hasattr(source, "read"):
        position = source.tell() if source.seekable() else None
        content = source.read()

        if position is not None:
            source.seek(position)

        return content

    raise TypeError(
        "Tipo de archivo no compatible."
    )


# ==========================================================
# 3. IDENTIFICAR TIPO DE HOJA
# ==========================================================

def identify_sheet(sheet_name: str) -> str | None:
    """
    Identifica el conjunto de datos a partir
    del nombre de la hoja.
    """

    normalized = normalize_key(sheet_name)

    if normalized is None:
        return None

    normalized = normalized.replace("_", " ")
    normalized = " ".join(normalized.split())

    return SHEET_ALIASES.get(normalized)


# ==========================================================
# 4. INSPECCIONAR LIBRO EXCEL
# ==========================================================

def inspect_workbook(source) -> dict:
    """
    Obtiene los nombres y tipos de hojas
    sin realizar transformaciones de registros.
    """

    content = get_file_bytes(source)

    workbook = pd.ExcelFile(
        BytesIO(content),
        engine="openpyxl"
    )

    sheets = []

    for sheet_name in workbook.sheet_names:
        sheets.append({
            "nombre_hoja": sheet_name,
            "tipo_detectado": identify_sheet(sheet_name),
            "reconocida": identify_sheet(sheet_name) is not None
        })

    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "total_hojas": len(sheets),
        "hojas": sheets
    }


# ==========================================================
# 5. EXTRAER UNA HOJA
# ==========================================================

def read_excel_sheet(
    source,
    sheet_name: str,
    header: int = 0
) -> pd.DataFrame:
    """
    Lee una hoja específica del Excel.

    Los registros se mantienen sin transformar.
    Solamente se normalizan los encabezados.
    """

    content = get_file_bytes(source)

    df = pd.read_excel(
        BytesIO(content),
        sheet_name=sheet_name,
        header=header,
        dtype=object,
        engine="openpyxl"
    )

    if df.empty:
        raise ValueError(
            f"La hoja '{sheet_name}' no contiene registros."
        )

    df = normalize_columns(df)

    # Guardar referencia al número de fila original
    df["_fila_excel"] = range(
        header + 2,
        header + 2 + len(df)
    )

    df["_hoja_origen"] = sheet_name

    return df


# ==========================================================
# 6. EXTRAER AUTOMÁTICAMENTE HOJAS RECONOCIDAS
# ==========================================================

def extract_workbook(source) -> dict[str, pd.DataFrame]:
    """
    Extrae los conjuntos de datos reconocidos
    dentro de un libro Excel.

    Devuelve:
        {
            "faltas": DataFrame,
            "sin_faltas": DataFrame,
            "recorridos": DataFrame
        }

    Una hoja no reconocida se omite.
    """

    content = get_file_bytes(source)

    workbook = pd.ExcelFile(
        BytesIO(content),
        engine="openpyxl"
    )

    results = {}

    for sheet_name in workbook.sheet_names:

        data_type = identify_sheet(sheet_name)

        if data_type is None:
            continue

        if data_type in results:
            raise ValueError(
                "Existen varias hojas para el tipo "
                f"'{data_type}'. Revise el archivo."
            )

        results[data_type] = read_excel_sheet(
            content,
            sheet_name
        )

    if not results:
        raise ValueError(
            "No se encontraron hojas reconocidas. "
            "Se esperan FALTAS, SIN FALTAS o RECORRIDOS."
        )

    return results
