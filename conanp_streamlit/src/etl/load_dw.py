
# test_load_dw.py

import os

from src.etl.load_staging import create_db_engine
from src.etl.load_dw import (
    inspect_dw_model,
    build_dw_inventory,
    load_dw,
)


DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_db_engine(DATABASE_URL)

try:
    print("\n=== INVENTARIO DW ===")

    inventory = build_dw_inventory(engine)
    print(inventory.to_string(index=False))

    print("\n=== ESTRUCTURA DE LAS TABLAS ===")

    model = inspect_dw_model(engine)

    for table_name, info in model.items():

        print(f"\nTABLA: {table_name}")

        print("Columnas:")
        for column in info["columnas"]:
            print(
                column["name"],
                column["type"],
                "nullable:",
                column["nullable"]
            )

        print(
            "Clave primaria:",
            info["clave_primaria"]
        )

        print(
            "Claves foráneas:",
            info["claves_foraneas"]
        )

    print("\n=== VALIDACIÓN DE CARGA ===")

    report = load_dw(
        engine,
        resultados_etl=None,
        confirm_write=False
    )

    print("Estado:", report["estado"])
    print(
        "Registros insertados:",
        report["registros_insertados"]
    )

finally:
    engine.dispose()
