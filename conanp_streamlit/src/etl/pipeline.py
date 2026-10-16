
# src/etl/pipeline.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .extract import (
    extract_workbook,
    inspect_workbook,
)

from .faltas import transform_faltas
from .sin_faltas import transform_sin_faltas
from .recorridos import transform_recorridos


# ==========================================================
# 1. CONFIGURACIÓN DE PROCESADORES
# ==========================================================

PROCESSORS = {
    "faltas": transform_faltas,
    "sin_faltas": transform_sin_faltas,
    "recorridos": transform_recorridos,
}


# ==========================================================
# 2. ESTRUCTURA DEL RESULTADO GENERAL
# ==========================================================

@dataclass
class PipelineResult:

    archivo_sha256: str

    hojas_detectadas: list[dict]

    resultados: dict[str, Any] = field(
        default_factory=dict
    )

    errores: dict[str, str] = field(
        default_factory=dict
    )

    resumen: pd.DataFrame = field(
        default_factory=pd.DataFrame
    )

    incidencias: pd.DataFrame = field(
        default_factory=pd.DataFrame
    )

    @property
    def exitoso(self) -> bool:
        return (
            len(self.resultados) > 0
            and len(self.errores) == 0
        )

    @property
    def listo_para_carga(self) -> bool:
        """
        Solo indica que las transformaciones terminaron
        sin errores ni rechazos.

        No sustituye la validación de catálogos, reglas
        de negocio ni compatibilidad con PostgreSQL.
        """
        if not self.exitoso:
            return False

        return all(
            len(resultado.rechazados) == 0
            for resultado in self.resultados.values()
        )


# ==========================================================
# 3. GENERACIÓN DE RESUMEN GENERAL
# ==========================================================

def build_summary(
    resultados: dict,
    errores: dict
) -> pd.DataFrame:

    rows = []

    for tipo, resultado in resultados.items():

        resumen = resultado.resumen

        rows.append({
            "tipo": tipo,
            "estado": "TRANSFORMADO",
            "originales": resumen.get(
                "registros_originales", 0
            ),
            "validos": resumen.get(
                "registros_validos", 0
            ),
            "rechazados": resumen.get(
                "registros_rechazados", 0
            ),
            "posibles_duplicados": resumen.get(
                "posibles_duplicados", 0
            ),
            "error": None,
        })

    for tipo, mensaje in errores.items():

        rows.append({
            "tipo": tipo,
            "estado": "ERROR",
            "originales": None,
            "validos": None,
            "rechazados": None,
            "posibles_duplicados": None,
            "error": mensaje,
        })

    return pd.DataFrame(
        rows,
        columns=[
            "tipo",
            "estado",
            "originales",
            "validos",
            "rechazados",
            "posibles_duplicados",
            "error",
        ]
    )


# ==========================================================
# 4. CONSOLIDACIÓN DE INCIDENCIAS
# ==========================================================

def build_incidents(resultados: dict) -> pd.DataFrame:

    frames = []

    for tipo, resultado in resultados.items():

        if resultado.incidencias.empty:
            continue

        df = resultado.incidencias.copy()

        df.insert(0, "tipo_fuente", tipo)

        frames.append(df)

    if not frames:
        return pd.DataFrame(
            columns=[
                "tipo_fuente",
                "fila_excel",
                "campo",
                "tipo",
                "descripcion",
            ]
        )

    return pd.concat(
        frames,
        ignore_index=True
    )


# ==========================================================
# 5. EJECUCIÓN PRINCIPAL DEL PIPELINE
# ==========================================================

def run_pipeline(source) -> PipelineResult:

    # Inspeccionar estructura y hash del archivo.
    metadata = inspect_workbook(source)

    # Extraer automáticamente las hojas reconocidas.
    extracted = extract_workbook(source)

    resultados = {}
    errores = {}

    # Ejecutar el ETL específico de cada fuente.
    for tipo, dataframe in extracted.items():

        processor = PROCESSORS.get(tipo)

        if processor is None:
            errores[tipo] = (
                "No existe un procesador configurado."
            )
            continue

        try:
            resultados[tipo] = processor(dataframe)

        except Exception as exc:
            # Aislar los errores de cada hoja.
            # No insertar nada en PostgreSQL.
            errores[tipo] = (
                f"{type(exc).__name__}: {exc}"
            )

    resumen = build_summary(
        resultados,
        errores
    )

    incidencias = build_incidents(
        resultados
    )

    return PipelineResult(
        archivo_sha256=metadata["sha256"],
        hojas_detectadas=metadata["hojas"],
        resultados=resultados,
        errores=errores,
        resumen=resumen,
        incidencias=incidencias,
    )
