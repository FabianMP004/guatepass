"""Integration test for the external registry seed.

The test is disabled by default because the external stack is created later
in the project. Enable it only with RUN_AWS_INTEGRATION=1 and provide:

    REGISTRY_STACK=guatepass-registry-dev
    REGISTRY_CSV=seeds/guatepass/registro_transito.csv
"""

from __future__ import annotations

import os
import csv
from pathlib import Path

import boto3
import pytest

from externo.seeds.seed import _resolve_table_name, seed_registry
from seeds.seed import seed_main


RUN_INTEGRATION = os.getenv("RUN_AWS_INTEGRATION") == "1"
STACK_NAME = os.getenv("REGISTRY_STACK", "")
CSV_PATH = os.getenv("REGISTRY_CSV", "seeds/guatepass/registro_transito.csv")
MAIN_STACK = os.getenv("GP_STACK", "")
INTEGRANTES_PATH = os.getenv(
    "INTEGRANTES_PATH", "seeds/guatepass/integrantes.csv"
)

EXTERNAL_INTEGRATION = pytest.mark.skipif(
    not RUN_INTEGRATION or not STACK_NAME,
    reason="Requiere RUN_AWS_INTEGRATION=1 y REGISTRY_STACK",
)


def _count_items(table) -> int:
    response = table.scan(Select="COUNT", ConsistentRead=True)
    count = int(response.get("Count", 0))

    while response.get("LastEvaluatedKey"):
        response = table.scan(
            Select="COUNT",
            ConsistentRead=True,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        count += int(response.get("Count", 0))

    return count


@EXTERNAL_INTEGRATION
def test_registry_seed_is_repeatable():
    assert STACK_NAME != "guatepass-dev", (
        "registro_transito.csv solo puede cargarse en el stack externo"
    )
    assert Path(CSV_PATH).is_file(), f"No existe el CSV: {CSV_PATH}"

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    table_name = _resolve_table_name(STACK_NAME, region)
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    before = _count_items(table)
    first = seed_registry(STACK_NAME, CSV_PATH)
    after_first = _count_items(table)
    second = seed_registry(STACK_NAME, CSV_PATH)
    after_second = _count_items(table)

    assert first["rows_read"] == 12
    assert second["rows_read"] == 12
    assert after_first == before + first["new_records"]
    assert after_second == after_first
    assert second["new_records"] == 0
    assert second["replayed_records"] == 12


def _stack_outputs(stack_name: str) -> dict[str, str]:
    response = boto3.client("cloudformation").describe_stacks(StackName=stack_name)
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in response["Stacks"][0].get("Outputs", [])
    }


def _table_count(table) -> int:
    response = table.scan(Select="COUNT", ConsistentRead=True)
    total = int(response.get("Count", 0))
    while response.get("LastEvaluatedKey"):
        response = table.scan(
            Select="COUNT",
            ConsistentRead=True,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        total += int(response.get("Count", 0))
    return total


def _pool_user_count(pool_id: str) -> int:
    client = boto3.client("cognito-idp")
    total = 0
    token = None
    while True:
        kwargs = {"UserPoolId": pool_id, "Limit": 60}
        if token:
            kwargs["PaginationToken"] = token
        response = client.list_users(**kwargs)
        total += len(response.get("Users", []))
        token = response.get("PaginationToken")
        if not token:
            return total


@pytest.mark.skipif(
    not RUN_INTEGRATION or not MAIN_STACK,
    reason="Requiere RUN_AWS_INTEGRATION=1 y GP_STACK",
)
def test_main_seed_is_repeatable():
    assert MAIN_STACK != "guatepass-registry-dev"
    result = seed_main(MAIN_STACK, INTEGRANTES_PATH)
    repeated = seed_main(MAIN_STACK, INTEGRANTES_PATH)

    assert result["usuarios_csv"] == 7
    assert result["peajes_csv"] == 4
    assert result["cognito_expected"] == 13
    assert result["registro_transito_cargado"] is False
    assert repeated["cognito_created"] == 0

    outputs = _stack_outputs(MAIN_STACK)
    dynamodb = boto3.resource("dynamodb")
    assert _table_count(dynamodb.Table(outputs["UsuariosTableName"])) >= 7
    assert _table_count(dynamodb.Table(outputs["PeajesTableName"])) >= 4
    assert _table_count(dynamodb.Table(outputs["TagsTableName"])) >= 4
    assert _pool_user_count(outputs["UserPoolId"]) >= 13

    clients = []
    with open("seeds/guatepass/clientes.csv", encoding="utf-8", newline="") as handle:
        clients = list(csv.DictReader(handle))
    andres = next(row for row in clients if row["tag_id"] == "TAG-003")
    user = dynamodb.Table(outputs["UsuariosTableName"]).get_item(
        Key={"placa": andres["placa"]}, ConsistentRead=True
    )["Item"]
    assert int(user["saldo_centavos"]) == 0
