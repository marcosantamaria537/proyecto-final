from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import pandas as pd

try:
    from sqlalchemy import text
except ImportError:  # Permite validar el Excel aun antes de instalar dependencias.
    def text(statement: str) -> str:
        return statement


UNKNOWN = "SIN ESPECIFICAR"


class ETLError(RuntimeError):
    """Error controlado durante la validación o carga ETL."""


@dataclass
class ETLPreview:
    kind: str
    filename: str
    file_hash: str
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    rejected: dict[str, pd.DataFrame] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid_rows(self) -> int:
        return sum(len(frame) for frame in self.frames.values())

    @property
    def rejected_rows(self) -> int:
        return sum(len(frame) for frame in self.rejected.values())


@dataclass
class ETLResult:
    inserted: int
    duplicates: int
    rejected: int
    details: dict[str, int]
    already_loaded: bool = False


SUPERVISION_SHEETS = ("FALTAS", "SIN FALTAS")
RECORRIDO_SHEET = "RECORRIDOS"


FALTAS_MAP = {
    "fecha_captura": ["BERNARDO ELIAS CAAMAL MADRIGAL CONANP <bernardo.caamal@conanp.gob.mx>", "MARCA TEMPORAL"],
    "fecha_supervision": ["FECHA DE LA SUPERVISION"],
    "anio": ["ANO"],
    "hora_inicio": ["HORA DE INICIO DE LA OBSERVACION"],
    "coordenadas": ["COORDENADAS"],
    "mes": ["MES"],
    "vehiculo": ["VEHICULO"],
    "vehiculo_ajeno": ["SI SE UTILIZO VEHICULO AJENO A CONANP ESCRIBIR NOMBRE PLACA O MATRICULA Y NOMBRE DEL PERSONAL AJENO"],
    "poligono": ["POLIGONO"],
    "subzona": ["SUBZONA"],
    "subpoligono": ["SUBPOLIGONO"],
    "actividad_terrestre": ["TERRESTRE"],
    "actividad_realizada": ["ACTIVIDAD REALIZADA"],
    "tipo_falta": ["TIPO DE FALTA"],
    "danio_ambiental": ["SI EXISTEDANO AMBIENTAL RESPONDER LO SIGUIENTE O PONER NO SI NO HAY ESTA FALTA"],
    "medida_tomada": ["MEDIDA TOMADA"],
    "embarcacion_autorizada": ["NOMBRE DE LA EMBARCACION INFRACTOR AUTORIZADO"],
    "titular_autorizado": ["COOPERATIVA O TITULAR AUTORIZADO SI NO ES EL CASO ELEGIR LA OPCION NO"],
    "embarcacion_no_autorizada": ["NOMBRE DE LA EMBARCACION INFRACTOR NO AUTORIZADA"],
    "titular_no_autorizado": ["COOPERATIVA O TITULAR NO AUTORIZADA"],
    "matricula_autorizada": ["MATRICULA E IDENTIFICACION"],
    "matricula_no_autorizada": ["MATRICULA E IDENTIFICACION NO AUTORIZADA"],
    "num_pax": ["PAX"],
    "num_tripulacion": ["TRIPULACION"],
    "nombre_capitan": ["NOMBRE DEL CAPITAN"],
    "otros_infractores": ["GUIA MARINERO PESCANDO DENTRO DEL ANPDOR BUZO ETC"],
    "folio_brazalete": ["FOLIO DE BRAZALETE"],
    "obs_infractor": ["OBSERVACIONES ADICIONALES ASOCIADOS AL INFRACTOR"],
    "obs_empresa": ["OBSERVACIONES ADICIONALES DE LA EMPRESA O COOPERATIVA"],
    "tipo_embarcacion": ["EMBARCACION"],
    "muelle_zarpe": ["MUELLE DONDE ZARPA"],
    "aseguramiento": ["HUBO ASEGURAMIENTO DE BIENES O PRODUCTOS"],
    "desc_aseguramiento": ["SI LA RESPUESTA ANTERIOR ES SI DESCRIBIR DE MANERA GENERAL LOS PRODUCTOS O BIENES ASEGURADOS"],
    "coord_intersectorial": ["SE TUVO COORDINACION INTERSECTORIAL SI LA RESPUESTA ES NEGATIVA ELEGIR LA OPCION NO"],
    "acompaniantes": ["SI LA PREGUNTA ANTERIOR FUE AFIRMATIVA ESCRIBA LOS NOMBRE DE LOS ACOMPANANTES"],
    "proceso_administrativo": ["PROCESO ADMINISTRATIVO"],
    "denuncias": ["DENUNCIAS O NOTIFICACIONES"],
    "archivo_1": ["ARCHIVOS 0"],
    "archivo_2": ["ARCHIVOS 1"],
    "archivo_3": ["ARCHIVOS 2"],
    "archivo_4": ["ACHIVOS 3", "ARCHIVOS 3"],
}

SIN_FALTAS_MAP = {
    "fecha_supervision": ["FECHA DE LA SUPERVISION"],
    "anio": ["ANO"],
    "coordenadas": ["COORDENADAS"],
    "mes": ["MES"],
    "vehiculo": ["VEHICULO"],
    "poligono": ["POLIGONO"],
    "subzona": ["SUBZONA"],
    "subpoligono": ["SUBPOLIGONO"],
    "actividad_terrestre": ["TERRESTRE"],
    "actividad_realizada": ["ACTIVIDAD REALIZADA"],
    "embarcacion": ["NOMBRE DE LA EMBARCACION"],
    "titular_autorizado": ["COOPERATIVA O TITULAR AUTORIZADO SI NO ES EL CASO ELEGIR LA OPCION NO"],
    "matricula": ["MATRICULA E IDENTIFICACION"],
    "num_pax": ["PAX"],
    "num_tripulacion": ["TRIPULACION"],
    "nombre_capitan": ["NOMBRE DEL CAPITAN"],
    "otros_tripulantes": ["GUIA MARINERO PESCANDO DENTRO DEL ANPDOR BUZO ETC"],
    "obs_supervision": ["OBSERVACIONES ADICIONALES ASOCIADOS AL INFRACTOR"],
    "obs_empresa": ["OBSERVACIONES ADICIONALES DE LA EMPRESA O COOPERATIVA"],
}

RECORRIDOS_MAP = {
    "fecha_captura": ["MARCA TEMPORAL"],
    "fecha_recorrido": ["FECHA"],
    "anio": ["ANO"],
    "hora_inicio": ["HORA DE INICIO"],
    "hora_fin": ["HORA DE FINALIZACION"],
    "mes": ["MES"],
    "poligono": ["POLIGONO"],
    "subzona": ["SUBZONA"],
    "subpoligono": ["SUBPOLIGONO"],
    "actividad_terrestre": ["TERRESTRE"],
    "personal_capitan": ["PERSONAL QUE REALIZA LA DILIGENCIA SI NO SE CUENTA CON LA INFORMACION MARCAR LA CASILLA NO CAPITAN CHOFER"],
    "personal_supervisor": ["PERSONAL QUE REALIZA LA DILIGENCIA SI NO SE CUENTA CON LA INFORMACION MARCAR LA CASILLA NO SUPERVISOR"],
    "personal_otro": ["SI LA RESPUESTA ANTERIOR FUE OTRO ESCRIBIR NOMBRE Y ACTVIDAD QUE REALIZO"],
    "vehiculo": ["VEHICULO"],
    "km_recorridos": ["KILOMETROS RECORRIDOS"],
    "vehiculo_ajeno": ["SI SE UTILIZO VEHICULO AJENO A CONANP ESCRIBIR NOMBRE PLACA O MATRICULA Y NOMBRE DEL PERSONAL AJENO"],
    "tipo_actividad": ["TIPO DE ACTIVIDAD"],
    "pesca_dentro_anp": ["ACTIVIDAD DE PESCANDO DENTRO DEL ANP"],
    "resguardo_objetos": ["HUBO RESGUARDO DE OBJETOS"],
    "coord_intersectorial": ["SE TUVO COORDINACION INTERSECTORIAL"],
    "acompaniantes": ["SI LA PREGUNTA ANTERIOR FUE AFIRMATIVA ESCRIBA LOS NOMBRE DE LOS ACOMPANANTES"],
    "nombre_autoridad": ["ESCRIBIR NOMBRE DE LA AUTORIDAD"],
    "observaciones": ["OBSERVACIONES"],
}


FAULT_RULES = {
    "f_brazalete": ("Falta de brazalete", [("BRAZALETE",)]),
    "f_acred_capitan": ("Falta de acreditación del capitán", [("ACREDITACION", "CAPITAN")]),
    "f_acred_guia": (
        "Falta de acreditación del guía",
        [("ACREDITACION", "GUIA"), ("ACREDITACION", "MARINERO"), ("ACREDITACION", "FOTOGRAFO")],
    ),
    "f_cred_capitan_vencida": ("Credencial del capitán vencida", [("CREDENCIAL", "CAPITAN", "VENCIDA")]),
    "f_cred_guia_vencida": (
        "Credencial del guía vencida",
        [("CREDENCIAL", "GUIA", "VENCIDA"), ("CREDENCIAL", "MARINERO", "VENCIDA")],
    ),
    "f_embarcacion_sin_aut": ("Embarcación sin autorización", [("EMBARCACION", "SIN AUTORIZACION")]),
    "f_embarcacion_fondeada": ("Embarcación fondeada", [("EMBARCACION", "FONDEADA")]),
    "f_pesca": ("Actividad de pesca", [("PESCA",), ("PESCANDO",)]),
    "f_cupo_excedido": ("Cupo excedido", [("CUPO", "EXCEDIDO"), ("NO RESPETAR", "CUPO")]),
    "f_zona_no_aut": ("Ingreso a zona no autorizada", [("ZONA", "NO AUTORIZADA")]),
    "f_actividad_no_permit": ("Actividad no permitida", [("ACTIVIDAD", "NO PERMITIDA")]),
    "f_horario": ("Incumplimiento de horario", [("HORARIO",)]),
    "f_bloqueador": ("Uso de bloqueador", [("BLOQUEADOR",)]),
    "f_chaleco": ("Falta de chaleco", [("CHALECO",)]),
    "f_dano_ambiental": ("Daño ambiental", [("DANO AMBIENTAL",)]),
    "f_remocion_pastos": ("Remoción de pastos marinos", [("REMOCION", "PASTOS")]),
    "f_alimentando": ("Alimentación de fauna", [("ALIMENT",)]),
    "f_aut_vencida": ("Autorización vencida", [("AUTORIZACION", "VENCIDA")]),
    "f_no_apoyo_supervision": ("Falta de apoyo a la supervisión", [("NO", "APOYO", "SUPERVISION")]),
    "f_distancia_arrecifes": ("Distancia inadecuada de arrecifes", [("DISTANCIA", "ARRECIF")]),
    "f_sin_nombre_matricula": ("Embarcación sin nombre o matrícula", [("SIN", "NOMBRE", "MATRICULA")]),
    "f_extraccion_fauna": ("Extracción de fauna", [("EXTRACCION", "FAUNA")]),
}


def _key(value: Any) -> str:
    value = "" if value is None else str(value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()


def _clean_text(value: Any, default: str | None = None) -> str | None:
    if value is None or pd.isna(value):
        return default
    cleaned = " ".join(str(value).replace("\u00a0", " ").split()).strip()
    return cleaned or default


def _db_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _time_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%H:%M:%S")
    return _clean_text(value)


def _rename_source(frame: pd.DataFrame, mapping: dict[str, list[str]]) -> pd.DataFrame:
    source = {_key(column): column for column in frame.columns}
    renamed: dict[str, str] = {}
    for target, aliases in mapping.items():
        for alias in aliases:
            column = source.get(_key(alias))
            if column is not None:
                renamed[column] = target
                break
    return frame.rename(columns=renamed)


def _select_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    for column in columns:
        result[column] = frame[column] if column in frame else None
    return result


def _frame_hash(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    def canonical(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        if isinstance(value, (datetime, pd.Timestamp, date)):
            return pd.Timestamp(value).isoformat()
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return _key(value)

    return frame[columns].apply(
        lambda row: hashlib.sha256(
            "\x1f".join(canonical(value) for value in row).encode("utf-8")
        ).hexdigest(),
        axis=1,
    )


def _classify_faults(value: Any) -> tuple[list[str], dict[str, int]]:
    raw = _clean_text(value)
    normalized = _key(raw)
    flags: dict[str, int] = {}
    names: list[str] = []
    for flag, (label, alternatives) in FAULT_RULES.items():
        matched = bool(normalized) and any(
            all(term in normalized for term in terms)
            for terms in alternatives
        )
        flags[flag] = int(matched)
        if matched:
            names.append(label)
    # Conserva una categoría desconocida en lugar de perder la incidencia.
    if raw and not names:
        names.append(raw)
    return names, flags


def _prepare_faltas(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    renamed = _rename_source(frame, FALTAS_MAP)
    columns = list(FALTAS_MAP)
    result = _select_columns(renamed, columns)
    result["fecha_supervision"] = pd.to_datetime(result["fecha_supervision"], errors="coerce")
    valid = result["fecha_supervision"].notna()
    rejected = result.loc[~valid].copy()
    result = result.loc[valid].copy()
    result["fecha_captura"] = pd.to_datetime(result["fecha_captura"], errors="coerce")
    result["fecha_registro"] = result["fecha_captura"]
    result["anio"] = result["fecha_supervision"].dt.year
    result["hora_inicio"] = result["hora_inicio"].map(_time_text)
    result["es_terrestre"] = result["actividad_terrestre"].map(
        lambda value: "SI" if _key(value) not in ("", "NO", _key(UNKNOWN)) else "NO"
    )
    result["num_pax"] = pd.to_numeric(result["num_pax"], errors="coerce")
    result["num_tripulacion"] = pd.to_numeric(result["num_tripulacion"], errors="coerce")
    result["tipo_falta_norm"] = result["tipo_falta"].map(_key)
    classifications = result["tipo_falta"].map(_classify_faults)
    for flag in FAULT_RULES:
        result[flag] = classifications.map(lambda item, name=flag: item[1][name])
    result["num_faltas"] = classifications.map(lambda item: len(item[0]))
    result["en_catalogo_embarcacion"] = None
    result["en_catalogo_titular"] = None
    result["_fault_names"] = classifications.map(lambda item: item[0])
    return result.reset_index(drop=True), rejected.reset_index(drop=True)


def _prepare_sin_faltas(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    renamed = _rename_source(frame, SIN_FALTAS_MAP)
    result = _select_columns(renamed, list(SIN_FALTAS_MAP))
    result["fecha_supervision"] = pd.to_datetime(result["fecha_supervision"], errors="coerce")
    valid = result["fecha_supervision"].notna()
    rejected = result.loc[~valid].copy()
    result = result.loc[valid].copy()
    result["anio"] = result["fecha_supervision"].dt.year
    result["es_terrestre"] = result["actividad_terrestre"].map(
        lambda value: "SI" if _key(value) not in ("", "NO", _key(UNKNOWN)) else "NO"
    )
    result["num_pax"] = pd.to_numeric(result["num_pax"], errors="coerce")
    result["num_tripulacion"] = pd.to_numeric(result["num_tripulacion"], errors="coerce")
    result["fecha_estimada"] = None
    result["en_catalogo_embarcacion"] = None
    result["en_catalogo_titular"] = None
    return result.reset_index(drop=True), rejected.reset_index(drop=True)


def _prepare_recorridos(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    renamed = _rename_source(frame, RECORRIDOS_MAP)
    result = _select_columns(renamed, list(RECORRIDOS_MAP))
    result["fecha_recorrido"] = pd.to_datetime(result["fecha_recorrido"], errors="coerce")
    valid = result["fecha_recorrido"].notna()
    rejected = result.loc[~valid].copy()
    result = result.loc[valid].copy()
    result["fecha_captura"] = pd.to_datetime(result["fecha_captura"], errors="coerce")
    result["anio"] = result["fecha_recorrido"].dt.year
    result["hora_inicio"] = result["hora_inicio"].map(_time_text)
    result["hora_fin"] = result["hora_fin"].map(_time_text)
    result["es_terrestre"] = result["actividad_terrestre"].map(
        lambda value: "SI" if _key(value) not in ("", "NO", _key(UNKNOWN)) else "NO"
    )
    result["km_recorridos"] = pd.to_numeric(result["km_recorridos"], errors="coerce").clip(lower=0)
    return result.reset_index(drop=True), rejected.reset_index(drop=True)


def preview_excel(file_bytes: bytes, filename: str, kind: str) -> ETLPreview:
    preview = ETLPreview(
        kind=kind,
        filename=filename,
        file_hash=hashlib.sha256(file_bytes).hexdigest(),
    )
    try:
        workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    except Exception as exc:
        preview.errors.append(f"No se pudo abrir el Excel: {exc}")
        return preview

    sheet_lookup = {_key(sheet): sheet for sheet in workbook.sheet_names}
    if kind == "supervisiones":
        available = [sheet for sheet in SUPERVISION_SHEETS if _key(sheet) in sheet_lookup]
        if not available:
            preview.errors.append("El archivo debe contener la hoja FALTAS o SIN FALTAS.")
            return preview
        for expected in available:
            actual = sheet_lookup[_key(expected)]
            source = pd.read_excel(io.BytesIO(file_bytes), sheet_name=actual)
            prepared, rejected = (
                _prepare_faltas(source) if expected == "FALTAS" else _prepare_sin_faltas(source)
            )
            preview.frames[expected] = prepared
            preview.rejected[expected] = rejected
    elif kind == "recorridos":
        actual = sheet_lookup.get(_key(RECORRIDO_SHEET))
        if actual is None:
            preview.errors.append("El archivo debe contener la hoja RECORRIDOS.")
            return preview
        source = pd.read_excel(io.BytesIO(file_bytes), sheet_name=actual)
        prepared, rejected = _prepare_recorridos(source)
        preview.frames[RECORRIDO_SHEET] = prepared
        preview.rejected[RECORRIDO_SHEET] = rejected
    else:
        preview.errors.append("Tipo de carga no reconocido.")

    if preview.rejected_rows:
        preview.warnings.append(
            f"Se excluirán {preview.rejected_rows:,} filas sin una fecha válida."
        )
    if not preview.valid_rows and not preview.errors:
        preview.errors.append("No se encontraron filas válidas para cargar.")
    return preview


def _ensure_control_tables(connection) -> None:
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS etl"))
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS etl.carga_archivo (
            carga_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tipo_carga VARCHAR(20) NOT NULL,
            nombre_archivo TEXT NOT NULL,
            sha256 CHAR(64) NOT NULL,
            filas_insertadas INTEGER NOT NULL DEFAULT 0,
            filas_duplicadas INTEGER NOT NULL DEFAULT 0,
            filas_rechazadas INTEGER NOT NULL DEFAULT 0,
            detalle JSONB NOT NULL DEFAULT '{}'::jsonb,
            cargado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tipo_carga, sha256)
        )
    """))
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS etl.registro_cargado (
            tipo_registro VARCHAR(20) NOT NULL,
            fila_hash CHAR(64) NOT NULL,
            carga_id BIGINT NOT NULL REFERENCES etl.carga_archivo(carga_id),
            fila_origen BIGINT NOT NULL,
            PRIMARY KEY (tipo_registro, fila_hash)
        )
    """))


def _to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def _existing_hashes(
    connection,
    record_type: str,
    staging_table: str,
    hash_columns: list[str],
) -> set[str]:
    rows = connection.execute(
        text("SELECT fila_hash FROM etl.registro_cargado WHERE tipo_registro = :tipo"),
        {"tipo": record_type},
    )
    known = {row[0] for row in rows}

    # También reconoce los registros cargados antes de instalar este módulo.
    # Las tablas staging existentes funcionan como historial de origen.
    quoted_columns = ", ".join(f'"{column}"' for column in hash_columns)
    existing = pd.read_sql(
        text(f"SELECT {quoted_columns} FROM staging.{staging_table}"),
        connection,
    )
    if not existing.empty:
        known.update(_frame_hash(existing, hash_columns).tolist())
    return known


def _insert_dates(connection, values: pd.Series) -> dict[date, int]:
    dates = sorted({pd.Timestamp(value).date() for value in values.dropna()})
    parameters = [
        {
            "fecha": value,
            "dia": value.day,
            "mes": value.month,
            "nombre_mes": (
                "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
                "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
            )[value.month - 1],
            "trimestre": (value.month - 1) // 3 + 1,
            "anio": value.year,
        }
        for value in dates
    ]
    if parameters:
        connection.execute(text("""
            INSERT INTO dw.dim_fecha (fecha, dia, mes, nombre_mes, trimestre, anio)
            VALUES (:fecha, :dia, :mes, :nombre_mes, :trimestre, :anio)
            ON CONFLICT (fecha) DO NOTHING
        """), parameters)
    rows = connection.execute(
        text("SELECT fecha_id, fecha FROM dw.dim_fecha")
    ) if dates else []
    return {row.fecha: row.fecha_id for row in rows}


def _insert_locations(connection, frame: pd.DataFrame) -> dict[tuple[str, str, str], int]:
    # Evita conjuntos directos: si un controlador devuelve un valor complejo,
    # no se intentará insertar un dict no hashable dentro de un set.
    triples_by_key: dict[str, tuple[str, str, str]] = {}
    for row in frame[["poligono", "subzona", "subpoligono"]].itertuples(index=False):
        triple = (
            _clean_text(row.poligono, UNKNOWN) or UNKNOWN,
            _clean_text(row.subzona, UNKNOWN) or UNKNOWN,
            _clean_text(row.subpoligono, UNKNOWN) or UNKNOWN,
        )
        triples_by_key.setdefault(json.dumps(triple, ensure_ascii=False), triple)

    parameters = [
        {"poligono": a, "subzona": b, "subpoligono": c}
        for a, b, c in sorted(triples_by_key.values())
    ]
    if parameters:
        connection.execute(text("""
            INSERT INTO dw.dim_ubicacion (poligono, subzona, subpoligono)
            VALUES (:poligono, :subzona, :subpoligono)
            ON CONFLICT (poligono, subzona, subpoligono) DO NOTHING
        """), parameters)
    rows = connection.execute(text("SELECT ubicacion_id, poligono, subzona, subpoligono FROM dw.dim_ubicacion"))
    return {(row.poligono, row.subzona, row.subpoligono): row.ubicacion_id for row in rows}


def _insert_activities(connection, rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str], int]:
    columns = (
        "actividad_terrestre",
        "actividad_realizada",
        "tipo_actividad",
        "es_terrestre",
    )
    unique_by_key: dict[str, tuple[str, str, str, str]] = {}
    for row in rows:
        values = tuple(
            _clean_text(row.get(column), UNKNOWN) or UNKNOWN
            for column in columns
        )
        unique_by_key.setdefault(json.dumps(values, ensure_ascii=False), values)

    parameters = [dict(zip(columns, values)) for values in sorted(unique_by_key.values())]
    if parameters:
        connection.execute(text("""
            INSERT INTO dw.dim_actividad
                (actividad_terrestre, actividad_realizada, tipo_actividad, es_terrestre)
            VALUES
                (:actividad_terrestre, :actividad_realizada, :tipo_actividad, :es_terrestre)
            ON CONFLICT (actividad_terrestre, actividad_realizada, tipo_actividad, es_terrestre)
            DO NOTHING
        """), parameters)
    result = connection.execute(text("""
        SELECT actividad_id, actividad_terrestre, actividad_realizada, tipo_actividad, es_terrestre
        FROM dw.dim_actividad
    """))
    return {
        (row.actividad_terrestre, row.actividad_realizada, row.tipo_actividad, row.es_terrestre): row.actividad_id
        for row in result
    }


def _next_origin(connection, fact_table: str, source: str | None = None) -> int:
    where = " WHERE fuente = :fuente" if source else ""
    row = connection.execute(
        text(f"SELECT COALESCE(MAX(fila_origen), 0) FROM dw.{fact_table}{where}"),
        {"fuente": source} if source else {},
    ).one()
    return int(row[0]) + 1


def _deduplicate(frame: pd.DataFrame, hash_columns: list[str], existing: set[str]) -> tuple[pd.DataFrame, int]:
    work = frame.copy()
    work["_etl_hash"] = _frame_hash(work, hash_columns)
    duplicate_mask = work["_etl_hash"].duplicated(keep="first") | work["_etl_hash"].isin(existing)
    return work.loc[~duplicate_mask].copy(), int(duplicate_mask.sum())


def _load_supervision_sheet(connection, sheet: str, frame: pd.DataFrame, carga_id: int) -> tuple[int, int]:
    record_type = sheet.replace(" ", "_")
    private_columns = {"_fault_names", "_etl_hash"}
    hash_columns = list(FALTAS_MAP) if sheet == "FALTAS" else list(SIN_FALTAS_MAP)
    staging_table = "faltas_raw" if sheet == "FALTAS" else "sin_faltas_raw"
    frame, duplicates = _deduplicate(
        frame,
        hash_columns,
        _existing_hashes(connection, record_type, staging_table, hash_columns),
    )
    if frame.empty:
        return 0, duplicates

    # La base de datos utiliza valores canónicos sin espacios en `fuente`
    # (FALTAS y SIN_FALTAS), aunque el nombre de la hoja sea "SIN FALTAS".
    start = _next_origin(connection, "fact_supervision", record_type)
    frame["_fila_origen"] = range(start, start + len(frame))

    staging_frame = frame.drop(
        columns=[column for column in private_columns if column in frame.columns]
    )
    staging_frame.to_sql(
        staging_table,
        connection,
        schema="staging",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=250,
    )

    date_map = _insert_dates(connection, frame["fecha_supervision"])
    location_map = _insert_locations(connection, frame)
    activities: list[dict[str, str]] = []
    for row in frame.itertuples(index=False):
        terrestrial = _clean_text(getattr(row, "actividad_terrestre", None), UNKNOWN)
        realized = _clean_text(getattr(row, "actividad_realizada", None), UNKNOWN)
        activities.append({
            "actividad_terrestre": terrestrial,
            "actividad_realizada": realized,
            "tipo_actividad": "SUPERVISION",
            "es_terrestre": "SI" if _key(terrestrial) not in ("", "NO", _key(UNKNOWN)) else "NO",
        })
    activity_map = _insert_activities(connection, activities)

    facts: list[dict[str, Any]] = []
    frame_records = frame.to_dict(orient="records")
    for position, row in enumerate(frame_records):
        location = (
            _clean_text(row.get("poligono"), UNKNOWN),
            _clean_text(row.get("subzona"), UNKNOWN),
            _clean_text(row.get("subpoligono"), UNKNOWN),
        )
        activity = activities[position]
        activity_key = tuple(activity[key] for key in ("actividad_terrestre", "actividad_realizada", "tipo_actividad", "es_terrestre"))
        if sheet == "FALTAS":
            boat = _clean_text(row.get("embarcacion_autorizada")) or _clean_text(row.get("embarcacion_no_autorizada"))
            holder = _clean_text(row.get("titular_autorizado")) or _clean_text(row.get("titular_no_autorizado"))
            registration = _clean_text(row.get("matricula_autorizada")) or _clean_text(row.get("matricula_no_autorizada"))
            fault_count = int(row.get("num_faltas") or 0)
        else:
            boat = _clean_text(row.get("embarcacion"))
            holder = _clean_text(row.get("titular_autorizado"))
            registration = _clean_text(row.get("matricula"))
            fault_count = 0
        facts.append({
            "fuente": record_type,
            "fila_origen": int(row["_fila_origen"]),
            "fecha_id": date_map[pd.Timestamp(row["fecha_supervision"]).date()],
            "ubicacion_id": location_map[location],
            "actividad_id": activity_map[activity_key],
            "hora_inicio": row.get("hora_inicio"),
            "coordenadas": _clean_text(row.get("coordenadas")),
            "vehiculo": _clean_text(row.get("vehiculo")),
            "embarcacion": boat,
            "titular_autorizado": holder,
            "matricula": registration,
            "num_pax": _db_value(row.get("num_pax")),
            "num_tripulacion": _db_value(row.get("num_tripulacion")),
            "num_faltas": fault_count,
            "hubo_falta": sheet == "FALTAS" and fault_count > 0,
            "fecha_estimada": row.get("fecha_estimada"),
            "en_catalogo_embarcacion": row.get("en_catalogo_embarcacion"),
            "en_catalogo_titular": row.get("en_catalogo_titular"),
        })
    connection.execute(text("""
        INSERT INTO dw.fact_supervision (
            fuente, fila_origen, fecha_id, ubicacion_id, actividad_id,
            hora_inicio, coordenadas, vehiculo, embarcacion, titular_autorizado,
            matricula, num_pax, num_tripulacion, num_faltas, hubo_falta,
            fecha_estimada, en_catalogo_embarcacion, en_catalogo_titular
        ) VALUES (
            :fuente, :fila_origen, :fecha_id, :ubicacion_id, :actividad_id,
            :hora_inicio, :coordenadas, :vehiculo, :embarcacion, :titular_autorizado,
            :matricula, :num_pax, :num_tripulacion, :num_faltas, :hubo_falta,
            :fecha_estimada, :en_catalogo_embarcacion, :en_catalogo_titular
        )
        ON CONFLICT (fuente, fila_origen) DO NOTHING
    """), facts)

    if sheet == "FALTAS":
        fact_rows = connection.execute(text("""
            SELECT supervision_id, fila_origen FROM dw.fact_supervision
            WHERE fuente = :fuente AND fila_origen BETWEEN :start AND :finish
        """), {"fuente": record_type, "start": start, "finish": start + len(frame) - 1})
        fact_map = {row.fila_origen: row.supervision_id for row in fact_rows}
        fault_names = sorted(dict.fromkeys(
            _clean_text(name, UNKNOWN) or UNKNOWN
            for names in frame["_fault_names"]
            for name in names
        ))
        if fault_names:
            catalog_rows = list(connection.execute(text(
                "SELECT tipo_falta_id, codigo, nombre FROM dw.dim_tipo_falta"
            )))
            catalog = {_key(row.nombre): row.tipo_falta_id for row in catalog_rows}
            catalog_codes = {row.codigo: row.tipo_falta_id for row in catalog_rows}
            new_faults = [
                name for name in fault_names
                if _key(name) not in catalog
                and (_key(name).replace(" ", "_")[:80] or "OTRA") not in catalog_codes
            ]
            if new_faults:
                connection.execute(text("""
                    INSERT INTO dw.dim_tipo_falta (codigo, nombre)
                    VALUES (:codigo, :nombre)
                    ON CONFLICT (codigo) DO NOTHING
                """), [{"codigo": _key(name).replace(" ", "_")[:80] or "OTRA", "nombre": name[:180]} for name in new_faults])
                catalog_rows = list(connection.execute(text(
                    "SELECT tipo_falta_id, codigo, nombre FROM dw.dim_tipo_falta"
                )))
                catalog = {_key(row.nombre): row.tipo_falta_id for row in catalog_rows}
                catalog_codes = {row.codigo: row.tipo_falta_id for row in catalog_rows}
            bridge: list[dict[str, int]] = []
            for row in frame_records:
                counts: dict[int, int] = {}
                for name in row["_fault_names"]:
                    fault_code = _key(name).replace(" ", "_")[:80] or "OTRA"
                    fault_id = catalog.get(_key(name)) or catalog_codes.get(fault_code)
                    if fault_id:
                        counts[fault_id] = counts.get(fault_id, 0) + 1
                for fault_id, count in counts.items():
                    bridge.append({"supervision_id": fact_map[int(row["_fila_origen"])], "tipo_falta_id": fault_id, "cantidad": count})
            if bridge:
                connection.execute(text("""
                    INSERT INTO dw.bridge_supervision_falta (supervision_id, tipo_falta_id, cantidad)
                    VALUES (:supervision_id, :tipo_falta_id, :cantidad)
                    ON CONFLICT (supervision_id, tipo_falta_id)
                    DO UPDATE SET cantidad = EXCLUDED.cantidad
                """), bridge)

    registry = [
        {"tipo": record_type, "hash": row["_etl_hash"], "carga": carga_id, "fila": int(row["_fila_origen"])}
        for row in frame_records
    ]
    connection.execute(text("""
        INSERT INTO etl.registro_cargado (tipo_registro, fila_hash, carga_id, fila_origen)
        VALUES (:tipo, :hash, :carga, :fila)
        ON CONFLICT (tipo_registro, fila_hash) DO NOTHING
    """), registry)
    return len(frame), duplicates


def _load_recorridos(connection, frame: pd.DataFrame, carga_id: int) -> tuple[int, int]:
    record_type = "RECORRIDOS"
    hash_columns = list(RECORRIDOS_MAP)
    frame, duplicates = _deduplicate(
        frame,
        hash_columns,
        _existing_hashes(connection, record_type, "recorridos_raw", hash_columns),
    )
    if frame.empty:
        return 0, duplicates
    start = _next_origin(connection, "fact_recorrido")
    frame["_fila_origen"] = range(start, start + len(frame))
    frame.drop(columns=["_etl_hash"]).to_sql(
        "recorridos_raw", connection, schema="staging", if_exists="append",
        index=False, method="multi", chunksize=250,
    )
    date_map = _insert_dates(connection, frame["fecha_recorrido"])
    location_map = _insert_locations(connection, frame)
    activities: list[dict[str, str]] = []
    for row in frame.itertuples(index=False):
        terrestrial = _clean_text(row.actividad_terrestre, UNKNOWN)
        activity_type = _clean_text(row.tipo_actividad, UNKNOWN)
        activities.append({
            "actividad_terrestre": terrestrial,
            "actividad_realizada": activity_type,
            "tipo_actividad": activity_type,
            "es_terrestre": "SI" if _key(terrestrial) not in ("", "NO", _key(UNKNOWN)) else "NO",
        })
    activity_map = _insert_activities(connection, activities)
    facts: list[dict[str, Any]] = []
    frame_records = frame.to_dict(orient="records")
    for position, row in enumerate(frame_records):
        location = (_clean_text(row.get("poligono"), UNKNOWN), _clean_text(row.get("subzona"), UNKNOWN), _clean_text(row.get("subpoligono"), UNKNOWN))
        activity = activities[position]
        activity_key = tuple(activity[key] for key in ("actividad_terrestre", "actividad_realizada", "tipo_actividad", "es_terrestre"))
        facts.append({
            "fila_origen": int(row["_fila_origen"]),
            "fecha_id": date_map[pd.Timestamp(row["fecha_recorrido"]).date()],
            "ubicacion_id": location_map[location],
            "actividad_id": activity_map[activity_key],
            "fecha_captura": _db_value(row.get("fecha_captura")),
            "hora_inicio": row.get("hora_inicio"),
            "hora_fin": row.get("hora_fin"),
            "personal_capitan": _clean_text(row.get("personal_capitan")),
            "personal_supervisor": _clean_text(row.get("personal_supervisor")),
            "personal_otro": _clean_text(row.get("personal_otro")),
            "vehiculo": _clean_text(row.get("vehiculo")),
            "vehiculo_ajeno": _clean_text(row.get("vehiculo_ajeno")),
            "km_recorridos": _db_value(row.get("km_recorridos")),
            "pesca_dentro_anp": _clean_text(row.get("pesca_dentro_anp")),
            "resguardo_objetos": _clean_text(row.get("resguardo_objetos")),
            "coordinacion_intersectorial": _clean_text(row.get("coord_intersectorial")),
            "acompanantes": _clean_text(row.get("acompaniantes")),
            "nombre_autoridad": _clean_text(row.get("nombre_autoridad")),
            "observaciones": _clean_text(row.get("observaciones")),
        })
    connection.execute(text("""
        INSERT INTO dw.fact_recorrido (
            fila_origen, fecha_id, ubicacion_id, actividad_id, fecha_captura,
            hora_inicio, hora_fin, personal_capitan, personal_supervisor,
            personal_otro, vehiculo, vehiculo_ajeno, km_recorridos,
            pesca_dentro_anp, resguardo_objetos, coordinacion_intersectorial,
            acompanantes, nombre_autoridad, observaciones
        ) VALUES (
            :fila_origen, :fecha_id, :ubicacion_id, :actividad_id, :fecha_captura,
            :hora_inicio, :hora_fin, :personal_capitan, :personal_supervisor,
            :personal_otro, :vehiculo, :vehiculo_ajeno, :km_recorridos,
            :pesca_dentro_anp, :resguardo_objetos, :coordinacion_intersectorial,
            :acompanantes, :nombre_autoridad, :observaciones
        ) ON CONFLICT (fila_origen) DO NOTHING
    """), facts)
    registry = [
        {"tipo": record_type, "hash": row["_etl_hash"], "carga": carga_id, "fila": int(row["_fila_origen"])}
        for row in frame_records
    ]
    connection.execute(text("""
        INSERT INTO etl.registro_cargado (tipo_registro, fila_hash, carga_id, fila_origen)
        VALUES (:tipo, :hash, :carga, :fila)
        ON CONFLICT (tipo_registro, fila_hash) DO NOTHING
    """), registry)
    return len(frame), duplicates


def load_preview(engine, preview: ETLPreview) -> ETLResult:
    if preview.errors:
        raise ETLError("El archivo contiene errores de validación.")
    with engine.begin() as connection:
        _ensure_control_tables(connection)
        existing = connection.execute(text("""
            SELECT carga_id FROM etl.carga_archivo
            WHERE tipo_carga = :tipo AND sha256 = :sha256
        """), {"tipo": preview.kind, "sha256": preview.file_hash}).first()
        if existing:
            return ETLResult(0, preview.valid_rows, preview.rejected_rows, {}, True)

        carga_id = connection.execute(text("""
            INSERT INTO etl.carga_archivo (
                tipo_carga, nombre_archivo, sha256,
                filas_insertadas, filas_duplicadas, filas_rechazadas
            ) VALUES (:tipo, :nombre, :sha256, 0, 0, :rechazadas)
            RETURNING carga_id
        """), {
            "tipo": preview.kind,
            "nombre": preview.filename,
            "sha256": preview.file_hash,
            "rechazadas": preview.rejected_rows,
        }).scalar_one()

        inserted = 0
        duplicates = 0
        details: dict[str, int] = {}
        for sheet, frame in preview.frames.items():
            try:
                if sheet in SUPERVISION_SHEETS:
                    added, repeated = _load_supervision_sheet(
                        connection, sheet, frame, carga_id
                    )
                else:
                    added, repeated = _load_recorridos(
                        connection, frame, carga_id
                    )
            except Exception as exc:
                raise ETLError(
                    f"Error al cargar la hoja {sheet}: {exc}"
                ) from exc
            inserted += added
            duplicates += repeated
            details[sheet] = added

        connection.execute(text("""
            UPDATE etl.carga_archivo
            SET filas_insertadas = :insertadas,
                filas_duplicadas = :duplicadas,
                detalle = CAST(:detalle AS jsonb)
            WHERE carga_id = :carga_id
        """), {
            "insertadas": inserted,
            "duplicadas": duplicates,
            "detalle": json.dumps(details),
            "carga_id": carga_id,
        })
    return ETLResult(inserted, duplicates, preview.rejected_rows, details)
