from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import tempfile
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


class DataLoadError(ValueError):
    pass


ALIASES = {
    "fecha": ["fecha", "fecha_supervision", "fecha_supervicion", "fecha_captura", "fecha_evento"],
    "hora": ["hora", "hora_supervision", "hora_evento", "hora_captura"],
    "tipo_falta": ["tipo_falta", "falta", "tipo_infraccion", "infraccion"],
    "poligono": ["poligono", "area", "zona", "anp"],
    "subzona": ["subzona", "sub_zona"],
    "subpoligono": ["subpoligono", "sub_poligono"],
    "medida_tomada": ["medida_tomada", "medida", "accion_tomada"],
    "personal_supervisor": ["personal_supervisor", "supervisor", "inspector"],
    "vehiculo": ["vehiculo", "unidad", "unidad_vehicular"],
    "km_recorridos": ["km_recorridos", "kilometros_recorridos", "kilometraje", "km"],
    "tipo_actividad": ["tipo_actividad", "actividad"],
    "pesca_dentro_anp": ["pesca_dentro_anp", "pesca_en_anp"],
    "resguardo_objetos": ["resguardo_objetos", "objetos_resguardados"],
    "embarcacion": ["embarcacion", "nombre_embarcacion", "matricula_embarcacion"],
    "recorrido_id": ["recorrido_id", "id_recorrido", "folio_recorrido", "folio", "id_evento"],
    "es_falta": ["es_falta", "hubo_falta", "con_falta", "infraccion_detectada"],
    "latitud": ["latitud", "latitude", "lat", "y"],
    "longitud": ["longitud", "longitude", "lon", "lng", "x"],
}


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", text)).strip("_").lower()


def _rename_aliases(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [normalize_name(column) for column in frame.columns]
    normalized_to_canonical = {
        alias: canonical for canonical, aliases in ALIASES.items() for alias in aliases
    }
    rename_map: dict[str, str] = {}
    occupied = set(frame.columns)
    for column in frame.columns:
        canonical = normalized_to_canonical.get(column)
        if canonical and canonical not in occupied:
            rename_map[column] = canonical
            occupied.add(canonical)
    return frame.rename(columns=rename_map)


def _parse_boolean(series: pd.Series) -> pd.Series:
    truthy = {"1", "si", "sí", "true", "verdadero", "x", "con falta", "falta"}
    return series.fillna(False).map(lambda value: normalize_name(value) in {normalize_name(v) for v in truthy})


def standardize_dataframe(frame: pd.DataFrame, source_name: str = "datos") -> pd.DataFrame:
    if frame.empty:
        return frame
    data = _rename_aliases(frame)
    data = data.dropna(axis=0, how="all").dropna(axis=1, how="all")

    if "fecha" not in data:
        raise DataLoadError(
            "No se encontró una columna de fecha. Usa FECHA, FECHA_SUPERVISION, "
            "FECHA_SUPERVICION o FECHA_CAPTURA."
        )
    data["fecha"] = pd.to_datetime(data["fecha"], errors="coerce", dayfirst=True)
    data = data.loc[data["fecha"].notna()].copy()
    if data.empty:
        raise DataLoadError("La fuente no contiene fechas válidas.")

    defaults = {
        "tipo_falta": "Sin clasificación",
        "poligono": "Sin identificar",
        "subzona": "Sin identificar",
        "subpoligono": "Sin identificar",
        "medida_tomada": "Sin registro",
        "embarcacion": "Sin registro",
        "km_recorridos": 0.0,
        "tabla_origen": source_name,
    }
    for column, default in defaults.items():
        if column not in data:
            data[column] = default
        else:
            data[column] = data[column].fillna(default)

    if "recorrido_id" not in data:
        data["recorrido_id"] = [f"{source_name}-{index + 1}" for index in range(len(data))]
    data["recorrido_id"] = data["recorrido_id"].fillna("").astype(str)

    if "es_falta" in data:
        data["es_falta"] = _parse_boolean(data["es_falta"])
    else:
        no_fault = {"", "sin_clasificacion", "sin_falta", "ninguna", "no", "n_a", "na"}
        data["es_falta"] = ~data["tipo_falta"].map(normalize_name).isin(no_fault)

    if "hora" in data:
        numeric_hour = pd.to_numeric(data["hora"], errors="coerce").where(lambda values: values.between(0, 23))
        parsed_time = pd.to_datetime(data["hora"].astype(str), errors="coerce", format="mixed")
        hour_from_column = numeric_hour.fillna(parsed_time.dt.hour)
    else:
        hour_from_column = pd.Series(np.nan, index=data.index)
    data["hora_num"] = hour_from_column.fillna(data["fecha"].dt.hour).fillna(0).astype(int).clip(0, 23)
    data["hora"] = data["hora_num"].map(lambda hour: f"{hour:02d}:00")

    data["km_recorridos"] = pd.to_numeric(data["km_recorridos"], errors="coerce").fillna(0).clip(lower=0)
    for coordinate in ["latitud", "longitud"]:
        if coordinate not in data:
            data[coordinate] = np.nan
        data[coordinate] = pd.to_numeric(data[coordinate], errors="coerce")

    data["anio"] = data["fecha"].dt.year
    data["mes_num"] = data["fecha"].dt.month
    data["mes"] = data["fecha"].dt.to_period("M").dt.to_timestamp()
    day_names = {
        0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
        4: "Viernes", 5: "Sábado", 6: "Domingo",
    }
    data["dia_semana"] = data["fecha"].dt.dayofweek.map(day_names)

    data["embarcacion_anonima"] = data["embarcacion"].astype(str).map(
        lambda value: "EMB-" + hashlib.sha256(value.strip().upper().encode("utf-8")).hexdigest()[:8].upper()
        if normalize_name(value) not in {"", "sin_registro", "nan"}
        else "Sin registro"
    )
    return data.reset_index(drop=True)


def _read_excel(content: bytes) -> pd.DataFrame:
    try:
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None)
    except Exception as exc:
        raise DataLoadError(f"No fue posible leer el Excel: {exc}") from exc
    frames = []
    errors = []
    for sheet_name, frame in sheets.items():
        try:
            standardized = standardize_dataframe(frame, sheet_name)
            standardized["tabla_origen"] = sheet_name
            frames.append(standardized)
        except DataLoadError as exc:
            errors.append(f"{sheet_name}: {exc}")
    if not frames:
        raise DataLoadError("Ninguna hoja del Excel pudo procesarse. " + " | ".join(errors))
    return pd.concat(frames, ignore_index=True, sort=False)


def _read_sqlite(content: bytes) -> pd.DataFrame:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp:
            temp.write(content)
            temporary_path = temp.name
        with sqlite3.connect(temporary_path) as connection:
            table_rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            frames = []
            for (table_name,) in table_rows:
                safe_name = table_name.replace('"', '""')
                raw = pd.read_sql_query(f'SELECT * FROM "{safe_name}"', connection)
                try:
                    standardized = standardize_dataframe(raw, table_name)
                    standardized["tabla_origen"] = table_name
                    frames.append(standardized)
                except DataLoadError:
                    continue
        if not frames:
            raise DataLoadError("La base SQLite no contiene una tabla con registros y una columna de fecha reconocible.")
        return pd.concat(frames, ignore_index=True, sort=False)
    except sqlite3.DatabaseError as exc:
        raise DataLoadError(f"El archivo SQLite no es válido: {exc}") from exc
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


def load_tabular_bytes(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return _read_excel(content)
    if suffix == ".csv":
        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                return standardize_dataframe(pd.read_csv(io.BytesIO(content), encoding=encoding), Path(filename).stem)
            except UnicodeDecodeError:
                continue
            except Exception as exc:
                raise DataLoadError(f"No fue posible procesar el CSV: {exc}") from exc
        raise DataLoadError("No fue posible identificar la codificación del CSV.")
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return _read_sqlite(content)
    raise DataLoadError("Formato no compatible. Usa Excel, CSV o SQLite.")


def load_geojson_bytes(content: bytes, filename: str) -> dict:
    try:
        if Path(filename).suffix.lower() == ".zip":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                candidates = [name for name in archive.namelist() if Path(name).suffix.lower() in {".geojson", ".json"}]
                if not candidates:
                    raise DataLoadError("El ZIP no contiene un archivo GeoJSON.")
                content = archive.read(candidates[0])
        geojson = json.loads(content.decode("utf-8-sig"))
        if geojson.get("type") != "FeatureCollection":
            raise DataLoadError("El archivo geográfico debe ser un GeoJSON FeatureCollection.")
        return geojson
    except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise DataLoadError(f"No fue posible procesar los polígonos: {exc}") from exc


def filter_events(
    data: pd.DataFrame,
    start_date: date,
    end_date: date,
    polygons: list[str] | None = None,
    fault_types: list[str] | None = None,
) -> pd.DataFrame:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    mask = data["fecha"].between(start, end)
    if polygons:
        mask &= data["poligono"].astype(str).isin(polygons)
    if fault_types:
        mask &= data["tipo_falta"].astype(str).isin(fault_types)
    return data.loc[mask].copy()


def quality_summary(data: pd.DataFrame) -> dict:
    inspected = ["fecha", "tipo_falta", "poligono", "subzona", "medida_tomada", "km_recorridos"]
    rows = []
    for column in inspected:
        if column not in data:
            continue
        missing = data[column].isna() | data[column].astype(str).str.lower().isin(
            ["", "nan", "sin identificar", "sin especificar", "sin clasificación", "sin registro"]
        )
        rows.append({"campo": column, "valores_faltantes": int(missing.sum()), "porcentaje": round(missing.mean() * 100, 1)})
    return {
        "registros": len(data),
        "fechas_validas_pct": float(data["fecha"].notna().mean() * 100),
        "poligonos_identificados_pct": float(
            (~data["poligono"].astype(str).str.strip().str.lower().isin(
                ["", "no", "sin identificar", "sin especificar", "nan"]
            )).mean() * 100
        ),
        "faltantes": pd.DataFrame(rows).sort_values("porcentaje", ascending=False),
    }


def generate_demo_data(rows: int = 720) -> pd.DataFrame:
    rng = np.random.default_rng(2026)
    polygons = [
        "Costa Occidental de Isla Mujeres",
        "Punta Cancún / Zona Hotelera",
        "Punta Nizuc",
    ]
    centers = {
        polygons[0]: (21.235, -86.735),
        polygons[1]: (21.130, -86.748),
        polygons[2]: (21.035, -86.785),
    }
    dates = pd.Timestamp("2019-01-01") + pd.to_timedelta(
        rng.integers(0, (pd.Timestamp("2026-12-31") - pd.Timestamp("2019-01-01")).days, rows), unit="D"
    )
    selected_polygons = rng.choice(polygons, rows, p=[0.28, 0.47, 0.25])
    has_fault = rng.random(rows) < 0.36
    fault_types = np.where(
        has_fault,
        rng.choice(["Documentación irregular", "Actividad no autorizada", "Pesca dentro del ANP", "Equipo no permitido"], rows),
        "Sin falta",
    )
    frame = pd.DataFrame(
        {
            "fecha": dates + pd.to_timedelta(rng.integers(6, 20, rows), unit="h"),
            "hora": rng.integers(6, 20, rows),
            "tipo_falta": fault_types,
            "poligono": selected_polygons,
            "subzona": rng.choice(["Uso público", "Aprovechamiento sustentable", "Preservación"], rows),
            "subpoligono": rng.choice(["A", "B", "C"], rows),
            "medida_tomada": np.where(has_fault, rng.choice(["Apercibimiento", "Acta", "Retiro preventivo"], rows), "Sin medida"),
            "embarcacion": [f"DEMO-{value:03d}" for value in rng.integers(1, 90, rows)],
            "km_recorridos": rng.gamma(3.5, 6.0, rows).round(1),
            "recorrido_id": [f"REC-{value:05d}" for value in range(1, rows + 1)],
            "es_falta": has_fault,
            "latitud": [centers[p][0] for p in selected_polygons] + rng.normal(0, 0.018, rows),
            "longitud": [centers[p][1] for p in selected_polygons] + rng.normal(0, 0.018, rows),
        }
    )
    return standardize_dataframe(frame, "Datos sintéticos")
