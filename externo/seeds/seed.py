"""Seed the private external traffic registry from registro_transito.csv.

Usage:
    python externo/seeds/seed.py \
        --stack guatepass-registry-dev \
        --csv seeds/guatepass/registro_transito.csv

If the CloudFormation output is not named RegistryTableName (or one of the
supported aliases), set REGISTRY_TABLE_OUTPUT_KEY to the exact output key.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError


EXPECTED_ROWS = 12
TABLE_OUTPUT_ALIASES = (
    "RegistryTableName",
    "RegistroTableName",
    "TrafficRegistryTableName",
    "ExternalRegistryTableName",
)


class SeedError(RuntimeError):
    """An expected validation or AWS configuration error."""


def _normalise_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _output_map(stack_name: str, region: str | None = None) -> dict[str, str]:
    client = boto3.client("cloudformation", region_name=region)
    try:
        response = client.describe_stacks(StackName=stack_name)
    except (ClientError, BotoCoreError) as exc:
        raise SeedError(f"No se pudo leer el stack {stack_name!r}: {exc}") from exc

    stacks = response.get("Stacks", [])
    if not stacks:
        raise SeedError(f"El stack {stack_name!r} no existe o no tiene descripción")

    return {
        str(item["OutputKey"]): str(item["OutputValue"])
        for item in stacks[0].get("Outputs", [])
        if item.get("OutputKey") and item.get("OutputValue")
    }


def _resolve_table_name(stack_name: str, region: str | None = None) -> str:
    outputs = _output_map(stack_name, region)

    requested_key = os.getenv("REGISTRY_TABLE_OUTPUT_KEY")
    if requested_key:
        if requested_key not in outputs:
            available = ", ".join(sorted(outputs)) or "ninguno"
            raise SeedError(
                f"REGISTRY_TABLE_OUTPUT_KEY={requested_key!r} no existe. "
                f"Outputs disponibles: {available}"
            )
        return outputs[requested_key]

    for key in TABLE_OUTPUT_ALIASES:
        if key in outputs:
            return outputs[key]

    candidates = [
        (key, value)
        for key, value in outputs.items()
        if "table" in _normalise_name(key)
        and any(token in _normalise_name(key) for token in ("registry", "registro", "traffic", "transito"))
    ]
    if len(candidates) == 1:
        return candidates[0][1]

    available = ", ".join(sorted(outputs)) or "ninguno"
    raise SeedError(
        "No se pudo identificar el Output de la tabla externa. "
        "Definí REGISTRY_TABLE_OUTPUT_KEY con el nombre exacto. "
        f"Outputs disponibles: {available}"
    )


def _read_csv(csv_path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    path = Path(csv_path)
    if not path.is_file():
        raise SeedError(f"No existe el CSV: {path}")

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            if not headers or any(not header or not header.strip() for header in headers):
                raise SeedError("El CSV no tiene encabezados válidos")

            rows = []
            for line_number, row in enumerate(reader, start=2):
                if None in row:
                    raise SeedError(f"Columnas adicionales en la fila {line_number}")
                if any(value is None for value in row.values()):
                    raise SeedError(f"Columnas faltantes en la fila {line_number}")
                if not any(value.strip() for value in row.values()):
                    raise SeedError(f"Fila vacía en la línea {line_number}")
                rows.append({key.strip(): value.strip() for key, value in row.items()})
    except UnicodeDecodeError as exc:
        raise SeedError("El CSV no está codificado en UTF-8") from exc

    if len(rows) != EXPECTED_ROWS:
        raise SeedError(
            f"Se esperaban {EXPECTED_ROWS} filas en {path.name}, pero se encontraron {len(rows)}"
        )

    return [header.strip() for header in headers], rows


def _parse_bool(value: str, field: str) -> bool | str:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value


def _parse_value(field: str, value: str) -> Any:
    """Preserve identifiers as strings; use Decimal for money fields."""
    lowered = field.strip().lower()

    if lowered in {"activo", "active", "registrado", "encontrado", "validado"}:
        return _parse_bool(value, field)

    money_markers = ("monto", "saldo", "tarifa", "precio", "importe", "total", "costo")
    if any(marker in lowered for marker in money_markers):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise SeedError(f"Monto inválido en {field!r}: {value!r}") from exc

    return value


def _canonical_payload(row: dict[str, str]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_fallback(row: dict[str, str]) -> str:
    digest = hashlib.sha256(_canonical_payload(row).encode("utf-8")).hexdigest()
    return digest


def _find_csv_field(headers: Iterable[str], key_name: str) -> str | None:
    wanted = _normalise_name(key_name)
    for header in headers:
        if _normalise_name(header) == wanted:
            return header
    return None


def _table_keys(table: Any) -> list[str]:
    try:
        return [str(key["AttributeName"]) for key in table.key_schema]
    except (AttributeError, KeyError, TypeError) as exc:
        raise SeedError("No se pudo leer la clave primaria de la tabla externa") from exc


def _item_for_row(row: dict[str, str], headers: list[str], key_names: list[str]) -> dict[str, Any]:
    item = {field: _parse_value(field, value) for field, value in row.items()}

    for key_name in key_names:
        field = _find_csv_field(headers, key_name)
        if field:
            if not row[field]:
                raise SeedError(f"La clave {key_name!r} está vacía")
            continue

        # A deterministic synthetic key is safe only for an explicitly named
        # record-id style partition key. For any other schema, fail loudly.
        if _normalise_name(key_name) in {
            "id",
            "registroid",
            "recordid",
            "transitoid",
            "eventid",
        }:
            item[key_name] = _stable_fallback(row)
            continue

        raise SeedError(
            f"La clave DynamoDB {key_name!r} no aparece en los encabezados del CSV. "
            "Revisá la plantilla del stack externo; no se generará una clave inventada."
        )

    return item


def _key_from_item(item: dict[str, Any], key_names: list[str]) -> dict[str, Any]:
    return {key: item[key] for key in key_names}


def seed_registry(stack_name: str, csv_path: str) -> dict[str, Any]:
    """Load the 12 registry records into the external stack's table."""
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    headers, rows = _read_csv(csv_path)
    table_name = _resolve_table_name(stack_name, region)

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    key_names = _table_keys(table)
    if not key_names:
        raise SeedError(f"La tabla {table_name!r} no tiene clave primaria detectable")

    items = [_item_for_row(row, headers, key_names) for row in rows]
    keys = [_key_from_item(item, key_names) for item in items]

    if len({json.dumps(key, default=str, sort_keys=True) for key in keys}) != len(keys):
        raise SeedError("El CSV contiene claves DynamoDB duplicadas")

    existing_before = 0
    items_to_create: list[dict[str, Any]] = []
    for item, key in zip(items, keys):
        try:
            response = table.get_item(Key=key, ConsistentRead=True)
        except (ClientError, BotoCoreError) as exc:
            raise SeedError(f"No se pudo consultar la tabla externa: {exc}") from exc
        existing = response.get("Item")
        if existing is not None:
            existing_before += 1
            if existing != item:
                raise SeedError(
                    f"El registro existente con clave {key!r} difiere del CSV. "
                    "No se sobrescribirá; usá un ambiente reconstruido para resetearlo."
                )
            continue
        items_to_create.append(item)

    try:
        with table.batch_writer(overwrite_by_pkeys=key_names) as batch:
            for item in items_to_create:
                batch.put_item(Item=item)
    except (ClientError, BotoCoreError) as exc:
        raise SeedError(f"No se pudo sembrar la tabla externa: {exc}") from exc

    return {
        "stack": stack_name,
        "table": table_name,
        "rows_read": len(rows),
        "rows_written": len(items_to_create),
        "existing_before": existing_before,
        "new_records": len(items_to_create),
        "replayed_records": existing_before,
        "key_fields": key_names,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", required=True, help="Nombre del stack externo")
    parser.add_argument("--csv", required=True, help="Ruta a registro_transito.csv")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = seed_registry(args.stack, args.csv)
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Keep unexpected errors visible to the CLI user.
        print(f"ERROR inesperado: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
