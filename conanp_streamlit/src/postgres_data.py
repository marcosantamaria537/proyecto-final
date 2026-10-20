from __future__ import annotations

import hashlib
import time

import numpy as np
import pandas as pd


class DatabaseLoadError(RuntimeError):
    pass


SUPERVISION_QUERY = """
SELECT
    fs.supervision_id, fs.fuente, d.fecha, fs.hora_inicio AS hora,
    u.poligono, u.subzona, u.subpoligono,
    a.actividad_terrestre, a.actividad_realizada, a.es_terrestre,
    fs.vehiculo, fs.embarcacion, fs.num_pax, fs.num_tripulacion,
    fs.num_faltas, fs.hubo_falta AS es_falta, vista.tipos_falta,
    COALESCE(raw.medida_tomada::TEXT, 'Sin registro') AS medida_tomada
FROM dw.fact_supervision fs
JOIN dw.dim_fecha d ON d.fecha_id = fs.fecha_id
JOIN dw.dim_ubicacion u ON u.ubicacion_id = fs.ubicacion_id
JOIN dw.dim_actividad a ON a.actividad_id = fs.actividad_id
JOIN dw.vw_supervisiones vista ON vista.supervision_id = fs.supervision_id
LEFT JOIN staging.faltas_raw raw
    ON fs.fuente = 'FALTAS' AND raw._fila_origen = fs.fila_origen
ORDER BY d.fecha, fs.supervision_id
"""

RECORRIDO_QUERY = """
SELECT
    fr.recorrido_id, d.fecha, fr.hora_inicio, fr.hora_fin,
    u.poligono, u.subzona, u.subpoligono,
    a.actividad_terrestre, a.tipo_actividad, a.es_terrestre,
    fr.vehiculo, fr.km_recorridos
FROM dw.fact_recorrido fr
JOIN dw.dim_fecha d ON d.fecha_id = fr.fecha_id
JOIN dw.dim_ubicacion u ON u.ubicacion_id = fr.ubicacion_id
JOIN dw.dim_actividad a ON a.actividad_id = fr.actividad_id
ORDER BY d.fecha, fr.recorrido_id
"""

FAULT_DETAIL_QUERY = """
SELECT
    fs.supervision_id, d.fecha, u.poligono, u.subzona, u.subpoligono,
    tf.nombre AS tipo_falta, b.cantidad
FROM dw.bridge_supervision_falta b
JOIN dw.fact_supervision fs ON fs.supervision_id = b.supervision_id
JOIN dw.dim_tipo_falta tf ON tf.tipo_falta_id = b.tipo_falta_id
JOIN dw.dim_fecha d ON d.fecha_id = fs.fecha_id
JOIN dw.dim_ubicacion u ON u.ubicacion_id = fs.ubicacion_id
ORDER BY d.fecha, fs.supervision_id
"""


def _derived_time_columns(data: pd.DataFrame, hour_column: str) -> pd.DataFrame:
    data = data.copy()
    data["fecha"] = pd.to_datetime(data["fecha"], errors="coerce")
    data["anio"] = data["fecha"].dt.year
    data["mes_num"] = data["fecha"].dt.month
    data["mes"] = data["fecha"].dt.to_period("M").dt.to_timestamp()
    names = {
        0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
        4: "Viernes", 5: "Sábado", 6: "Domingo",
    }
    data["dia_semana"] = data["fecha"].dt.dayofweek.map(names)
    if hour_column in data:
        parsed = pd.to_datetime(
            data[hour_column].fillna("").astype(str),
            errors="coerce", format="mixed",
        )
        data["hora_num"] = parsed.dt.hour.fillna(0).astype(int)
    else:
        data["hora_num"] = 0
    return data


def _anonymous_boat(value: object) -> str:
    if value is None or pd.isna(value) or not str(value).strip():
        return "Sin registro"
    digest = hashlib.sha256(str(value).strip().upper().encode("utf-8")).hexdigest()
    return "EMB-" + digest[:8].upper()


def load_postgres_data(connection) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ultimo_error = None

    for intento in range(4):
        try:
            supervisions = connection.query(
                SUPERVISION_QUERY,
                ttl="10m",
                show_spinner="Conectando con PostgreSQL..."
            )
            recorridos = connection.query(
                RECORRIDO_QUERY,
                ttl="10m",
                show_spinner=False
            )
            faults = connection.query(
                FAULT_DETAIL_QUERY,
                ttl="10m",
                show_spinner=False
            )
            break
        except Exception as exc:
            ultimo_error = exc
            if intento == 3:
                raise DatabaseLoadError(
                    "No fue posible conectar con PostgreSQL después de varios intentos."
                ) from ultimo_error
            try:
                connection.reset()
            except Exception:
                pass
            time.sleep(4)
            
    supervisions = _derived_time_columns(supervisions, "hora")
    recorridos = _derived_time_columns(recorridos, "hora_inicio")
    faults = _derived_time_columns(faults, "hora")
    supervisions["num_faltas"] = pd.to_numeric(
        supervisions["num_faltas"], errors="coerce"
    ).fillna(0).astype(int)
    supervisions["es_falta"] = supervisions["es_falta"].fillna(False).astype(bool)
    supervisions["embarcacion_anonima"] = (
    supervisions["embarcacion"]
    .fillna("Sin registro")
    .astype(str)
    .str.strip()
    .replace("", "Sin registro")
)
    supervisions["tabla_origen"] = supervisions["fuente"]
    supervisions["tipo_falta"] = supervisions["tipos_falta"]
    supervisions["km_recorridos"] = 0.0
    supervisions["latitud"] = np.nan
    supervisions["longitud"] = np.nan
    recorridos["km_recorridos"] = pd.to_numeric(
        recorridos["km_recorridos"], errors="coerce"
    ).fillna(0).clip(lower=0)
    faults["cantidad"] = pd.to_numeric(
        faults["cantidad"], errors="coerce"
    ).fillna(0).astype(int)
    return supervisions, recorridos, faults

