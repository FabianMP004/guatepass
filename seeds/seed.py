#!/usr/bin/env python3
"""Seed the GuatePass main stack.

The script reads physical resource names from CloudFormation Outputs and is
safe to run repeatedly. Existing user balances, versions, tag activation
states, and Cognito passwords are never reset by a normal rerun.

Usage:
    read -rsp "Demo password: " GP_DEMO_PASSWORD; echo
    export GP_DEMO_PASSWORD
    python seeds/seed.py --stack guatepass-dev \
        --integrantes seeds/guatepass/integrantes.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


EXPECTED_OUTPUTS = {
    "UsuariosTableName",
    "PeajesTableName",
    "TagsTableName",
    "UserPoolId",
}
EXPECTED_GROUPS = ("conductor", "operador", "atencion", "administrador")


class SeedError(RuntimeError):
    """A validation or AWS error that should stop the seed safely."""


def _region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def _outputs(stack_name: str) -> dict[str, str]:
    client = boto3.client("cloudformation", region_name=_region())
    try:
        result = client.describe_stacks(StackName=stack_name)
    except (ClientError, BotoCoreError) as exc:
        raise SeedError(f"No se pudo leer el stack {stack_name!r}: {exc}") from exc

    stacks = result.get("Stacks", [])
    if not stacks:
        raise SeedError(f"No existe el stack {stack_name!r}")

    outputs = {
        str(item["OutputKey"]): str(item["OutputValue"])
        for item in stacks[0].get("Outputs", [])
        if item.get("OutputKey") and item.get("OutputValue")
    }
    missing = sorted(EXPECTED_OUTPUTS - outputs.keys())
    if missing:
        raise SeedError(f"Faltan Outputs requeridos: {', '.join(missing)}")
    return outputs


def _read_csv(path: Path, expected_rows: int, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise SeedError(f"No existe el archivo {path}")

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = [header.strip() for header in (reader.fieldnames or [])]
            if not headers or any(not header for header in headers):
                raise SeedError(f"{path}: encabezados inválidos")
            if not required.issubset(headers):
                missing = sorted(required - set(headers))
                raise SeedError(f"{path}: faltan columnas {missing}")

            rows: list[dict[str, str]] = []
            for line, raw in enumerate(reader, start=2):
                if None in raw or any(value is None for value in raw.values()):
                    raise SeedError(f"{path}: columnas incompletas en línea {line}")
                row = {key.strip(): value.strip() for key, value in raw.items()}
                if not any(row.values()):
                    raise SeedError(f"{path}: fila vacía en línea {line}")
                rows.append(row)
    except UnicodeDecodeError as exc:
        raise SeedError(f"{path}: no está codificado en UTF-8") from exc

    if len(rows) != expected_rows:
        raise SeedError(
            f"{path}: se esperaban {expected_rows} filas y se encontraron {len(rows)}"
        )
    return rows


def _unique(rows: list[dict[str, str]], field: str, label: str) -> None:
    values = [row[field] for row in rows]
    if any(not value for value in values):
        raise SeedError(f"{label}: hay valores vacíos en {field}")
    if len(set(values)) != len(values):
        raise SeedError(f"{label}: {field} contiene duplicados")


def _bool(value: str, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise SeedError(f"{field}: booleano inválido {value!r}; use true o false")


def _cents(value: str, field: str) -> int:
    try:
        amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise SeedError(f"{field}: monto inválido {value!r}") from exc
    if amount < 0:
        raise SeedError(f"{field}: no puede ser negativo")
    return int(amount * 100)


def _integrantes(path: Path) -> dict[str, str]:
    rows = _read_csv(path, expected_rows=3, required={"integrante", "carnet", "username"})
    _unique(rows, "integrante", "integrantes.csv")
    _unique(rows, "username", "integrantes.csv")
    result: dict[str, str] = {}
    for row in rows:
        carnet = row["carnet"].strip()
        if not carnet or "CARNET_" in carnet.upper() or "REAL" in carnet.upper():
            raise SeedError(
                f"integrantes.csv: el carné de {row['integrante']!r} todavía no es real"
            )
        result[row["username"].strip()] = carnet
    return result


def _attrs(user: dict[str, Any]) -> dict[str, str]:
    return {str(item["Name"]): str(item["Value"]) for item in user.get("UserAttributes", [])}


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return value.strip("-") or "item"


def _list_groups(client: Any, pool_id: str) -> set[str]:
    groups: set[str] = set()
    token = None
    while True:
        kwargs = {"UserPoolId": pool_id}
        if token:
            kwargs["NextToken"] = token
        response = client.list_groups(**kwargs)
        groups.update(group["GroupName"] for group in response.get("Groups", []))
        token = response.get("NextToken")
        if not token:
            return groups


def _ensure_groups(client: Any, pool_id: str) -> None:
    existing = _list_groups(client, pool_id)
    for group in EXPECTED_GROUPS:
        if group not in existing:
            client.create_group(UserPoolId=pool_id, GroupName=group)


def _ensure_cognito_user(
    client: Any,
    pool_id: str,
    username: str,
    group: str,
    attributes: dict[str, str],
    password: str,
) -> tuple[str, bool]:
    created = False
    try:
        response = client.admin_get_user(UserPoolId=pool_id, Username=username)
    except client.exceptions.UserNotFoundException:
        response = client.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            UserAttributes=[{"Name": key, "Value": value} for key, value in attributes.items()],
            MessageAction="SUPPRESS",
        )
        client.admin_set_user_password(
            UserPoolId=pool_id,
            Username=username,
            Password=password,
            Permanent=True,
        )
        created = True
    except (ClientError, BotoCoreError) as exc:
        raise SeedError(f"No se pudo consultar el usuario Cognito {username}: {exc}") from exc

    current = _attrs(response)
    mismatches = {
        key: (current.get(key), value)
        for key, value in attributes.items()
        if key in current and current[key] != value
    }
    if mismatches:
        raise SeedError(f"Atributos distintos para Cognito {username}: {mismatches}")

    missing = [
        {"Name": key, "Value": value}
        for key, value in attributes.items()
        if key not in current
    ]
    if missing:
        client.admin_update_user_attributes(
            UserPoolId=pool_id,
            Username=username,
            UserAttributes=missing,
        )

    client.admin_add_user_to_group(
        UserPoolId=pool_id,
        Username=username,
        GroupName=group,
    )

    sub = _attrs(response).get("sub")
    if not sub:
        refreshed = client.admin_get_user(UserPoolId=pool_id, Username=username)
        sub = _attrs(refreshed).get("sub")
    if not sub:
        raise SeedError(f"Cognito no devolvió sub para {username}")
    return sub, created


def _existing(table: Any, key_name: str, key_value: str) -> dict[str, Any] | None:
    response = table.get_item(Key={key_name: key_value}, ConsistentRead=True)
    return response.get("Item")


def _put_if_absent(table: Any, item: dict[str, Any], key_name: str) -> bool:
    existing = _existing(table, key_name, str(item[key_name]))
    if existing is not None:
        return False
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(#pk)",
            ExpressionAttributeNames={"#pk": key_name},
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def _ensure_tag(table: Any, users_table: Any, tag_id: str, placa: str) -> tuple[bool, bool]:
    user = _existing(users_table, "placa", placa)
    if user is None:
        raise SeedError(f"No existe el usuario {placa} al asociar {tag_id}")
    if user.get("tag_id") not in (None, tag_id):
        raise SeedError(f"{placa} ya tiene otro tag asociado")

    existing_tag = _existing(table, "tag_id", tag_id)
    if existing_tag is not None:
        if existing_tag.get("placa") != placa:
            raise SeedError(f"{tag_id} ya está asociado a otra placa")
        tag_created = False
        if user.get("tag_id") is None:
            users_table.update_item(
                Key={"placa": placa},
                UpdateExpression="SET #tag = :tag",
                ConditionExpression="attribute_not_exists(#tag) OR #tag = :tag",
                ExpressionAttributeNames={"#tag": "tag_id"},
                ExpressionAttributeValues={":tag": tag_id},
            )
        return tag_created, user.get("tag_id") is not None
    elif user.get("tag_id") == tag_id:
        # Recovery path for an interrupted earlier run: the user link exists
        # but the tag item was not committed. Create only the missing item.
        try:
            table.put_item(
                Item={"tag_id": tag_id, "placa": placa, "activo": True},
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": "tag_id"},
            )
            return True, True
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            existing_tag = _existing(table, "tag_id", tag_id)
            if existing_tag is None or existing_tag.get("placa") != placa:
                raise SeedError(f"No se pudo recuperar la asociación {tag_id} con {placa}")
            return False, True
    else:
        tag_created = False
        try:
            users_table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": users_table.name,
                            "Key": {"placa": {"S": placa}},
                            "UpdateExpression": "SET #tag = if_not_exists(#tag, :tag)",
                            "ConditionExpression": "attribute_exists(#pk) AND (attribute_not_exists(#tag) OR #tag = :tag)",
                            "ExpressionAttributeNames": {"#pk": "placa", "#tag": "tag_id"},
                            "ExpressionAttributeValues": {":tag": {"S": tag_id}},
                        }
                    },
                    {
                        "Put": {
                            "TableName": table.name,
                            "Item": {"tag_id": {"S": tag_id}, "placa": {"S": placa}, "activo": {"BOOL": True}},
                            "ConditionExpression": "attribute_not_exists(#pk)",
                            "ExpressionAttributeNames": {"#pk": "tag_id"},
                        }
                    },
                ]
            )
            tag_created = True
        except users_table.meta.client.exceptions.TransactionCanceledException as exc:
            # A concurrent/repeated run may have created the tag. Verify the
            # resulting association instead of overwriting it.
            existing_tag = _existing(table, "tag_id", tag_id)
            if existing_tag is None or existing_tag.get("placa") != placa:
                reasons = exc.response.get("CancellationReasons", [])
                detail = "; ".join(
                    str(reason.get("Code", "UNKNOWN")) for reason in reasons
                )
                raise SeedError(
                    f"No se pudo asociar {tag_id} con {placa}"
                    + (f" (motivos: {detail})" if detail else "")
                )
    return tag_created, user.get("tag_id") is not None


def seed_main(stack_name: str, integrantes_path: str) -> dict[str, Any]:
    """Seed main GuatePass data and Cognito users idempotently."""
    password = os.getenv("GP_DEMO_PASSWORD")
    if not password:
        raise SeedError("Definí GP_DEMO_PASSWORD sin escribir la contraseña en el código")

    outputs = _outputs(stack_name)
    base = Path(integrantes_path).resolve().parent
    clients_rows = _read_csv(
        base / "clientes.csv",
        expected_rows=7,
        required={"placa", "nombre", "nit", "email", "telefono", "tiene_tag", "tag_id", "saldo_disponible"},
    )
    toll_rows = _read_csv(
        base / "peajes.csv",
        expected_rows=4,
        required={"peaje_id", "nombre", "ubicacion", "tarifa_base"},
    )
    carnet_by_username = _integrantes(Path(integrantes_path).resolve())
    _unique(clients_rows, "placa", "clientes.csv")
    _unique(toll_rows, "peaje_id", "peajes.csv")
    tagged_rows = [row for row in clients_rows if _bool(row["tiene_tag"], "tiene_tag")]
    if len(tagged_rows) != 4:
        raise SeedError(
            f"clientes.csv: se esperaban 4 clientes con tag activo y se encontraron {len(tagged_rows)}"
        )
    _unique(tagged_rows, "tag_id", "clientes.csv")

    expected_usernames = {
        *(f"conductor_{_slug(row['placa'])}" for row in clients_rows),
        *(f"operador_{_slug(row['peaje_id'])}" for row in toll_rows),
        "atencion_1",
        "administrador_1",
    }
    unknown_usernames = sorted(set(carnet_by_username) - expected_usernames)
    if unknown_usernames:
        raise SeedError(
            "integrantes.csv contiene usernames que este seed no crea: "
            + ", ".join(unknown_usernames)
        )

    dynamodb = boto3.resource("dynamodb", region_name=_region())
    users_table = dynamodb.Table(outputs["UsuariosTableName"])
    tolls_table = dynamodb.Table(outputs["PeajesTableName"])
    tags_table = dynamodb.Table(outputs["TagsTableName"])
    cognito = boto3.client("cognito-idp", region_name=_region())
    _ensure_groups(cognito, outputs["UserPoolId"])

    created_cognito = 0
    user_subs: dict[str, str] = {}

    for row in clients_rows:
        username = f"conductor_{_slug(row['placa'])}"
        attrs = {
            "custom:placa": row["placa"],
        }
        if row.get("email"):
            attrs["email"] = row["email"]
        if username in carnet_by_username:
            attrs["custom:carnet"] = carnet_by_username[username]
        sub, created = _ensure_cognito_user(
            cognito, outputs["UserPoolId"], username, "conductor", attrs, password
        )
        user_subs[row["placa"]] = sub
        created_cognito += int(created)

        user_item = {
            "placa": row["placa"],
            "nombre": row["nombre"],
            "nit": row["nit"],
            "NIT": row["nit"],
            "email": row["email"],
            "telefono": row["telefono"],
            "saldo_centavos": _cents(row["saldo_disponible"], "saldo_disponible"),
            "version": 0,
            "sub": sub,
        }
        if username in carnet_by_username:
            user_item["carnet"] = carnet_by_username[username]
        if _bool(row["tiene_tag"], "tiene_tag"):
            if not row["tag_id"]:
                raise SeedError(f"{row['placa']}: tiene_tag=true pero tag_id está vacío")
        elif row["tag_id"]:
            raise SeedError(f"{row['placa']}: tiene_tag=false pero tag_id no está vacío")

        _put_if_absent(users_table, user_item, "placa")

    for row in toll_rows:
        item = {
            "peaje_id": row["peaje_id"],
            "nombre": row["nombre"],
            "ubicacion": row["ubicacion"],
            "tarifa_base_centavos": _cents(row["tarifa_base"], "tarifa_base"),
        }
        _put_if_absent(tolls_table, item, "peaje_id")

        username = f"operador_{_slug(row['peaje_id'])}"
        attrs = {"custom:peaje_id": row["peaje_id"]}
        if username in carnet_by_username:
            attrs["custom:carnet"] = carnet_by_username[username]
        _, created = _ensure_cognito_user(
            cognito, outputs["UserPoolId"], username, "operador", attrs, password
        )
        created_cognito += int(created)

    special_users = (
        ("atencion_1", "atencion"),
        ("administrador_1", "administrador"),
    )
    for username, group in special_users:
        attrs = {}
        if username in carnet_by_username:
            attrs["custom:carnet"] = carnet_by_username[username]
        _, created = _ensure_cognito_user(
            cognito, outputs["UserPoolId"], username, group, attrs, password
        )
        created_cognito += int(created)

    created_tags = 0
    for row in clients_rows:
        if _bool(row["tiene_tag"], "tiene_tag"):
            created, _ = _ensure_tag(
                tags_table, users_table, row["tag_id"], row["placa"]
            )
            created_tags += int(created)

    return {
        "stack": stack_name,
        "usuarios_csv": len(clients_rows),
        "peajes_csv": len(toll_rows),
        "tags_created": created_tags,
        "cognito_created": created_cognito,
        "cognito_expected": len(clients_rows) + len(toll_rows) + 2,
        "registro_transito_cargado": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--integrantes", required=True)
    args = parser.parse_args()

    try:
        result = seed_main(args.stack, args.integrantes)
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR inesperado: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
