"""Integration test for the external registry seed.

The test is disabled by default because the external stack is created later
in the project. Enable it only with RUN_AWS_INTEGRATION=1 and provide:

    REGISTRY_STACK=guatepass-registry-dev
    REGISTRY_CSV=seeds/guatepass/registro_transito.csv
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
import pytest

from externo.seeds.seed import _resolve_table_name, seed_registry


RUN_INTEGRATION = os.getenv("RUN_AWS_INTEGRATION") == "1"
STACK_NAME = os.getenv("REGISTRY_STACK", "")
CSV_PATH = os.getenv("REGISTRY_CSV", "seeds/guatepass/registro_transito.csv")

pytestmark = pytest.mark.skipif(
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
