from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

import boto3
import pytest
from boto3.dynamodb.conditions import Attr

from common.models import make_event


RUN_INTEGRATION = os.getenv("RUN_AWS_INTEGRATION") == "1"
REGISTRY_STACK = os.getenv("REGISTRY_STACK", "")
MAIN_STACK = os.getenv("GP_STACK", "")
IDEMPOTENCY_PASO_ID = os.getenv("REGISTRY_IDEMPOTENCY_PASO_ID", "")

INTEGRATION = pytest.mark.skipif(
    not RUN_INTEGRATION
    or not REGISTRY_STACK
    or not MAIN_STACK
    or not IDEMPOTENCY_PASO_ID,
    reason=(
        "Requiere RUN_AWS_INTEGRATION=1, REGISTRY_STACK, "
        "GP_STACK y REGISTRY_IDEMPOTENCY_PASO_ID"
    ),
)


def _outputs(stack_name):
    cf = boto3.client("cloudformation", region_name="us-east-1")
    response = cf.describe_stacks(StackName=stack_name)
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in response["Stacks"][0].get("Outputs", [])
    }


def _request(url, token=None):
    headers = {"Accept": "application/json"}

    if token:
        headers["Authorization"] = f"Basic {token}"

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@INTEGRATION
def test_registry_api_contract():
    outputs = _outputs(REGISTRY_STACK)
    secrets = boto3.client("secretsmanager", region_name="us-east-1")

    secret = json.loads(
        secrets.get_secret_value(
            SecretId=outputs["RegistroSecretArn"]
        )["SecretString"]
    )

    valid_token = base64.b64encode(
        f'{secret["username"]}:{secret["password"]}'.encode()
    ).decode()

    invalid_token = base64.b64encode(
        f'{secret["username"]}:incorrecta'.encode()
    ).decode()

    base_url = outputs["RegistroApiUrl"].rstrip("/")

    status, body = _request(
        f"{base_url}/registro/P-888EFG",
        valid_token,
    )
    assert status == 200
    assert body["placa"] == "P-888EFG"

    status, body = _request(
        f"{base_url}/registro/P-000XXX",
        valid_token,
    )
    assert status == 404
    assert body["codigo"] == "PLACA_NO_ENCONTRADA"

    status, body = _request(
        f"{base_url}/registro/P-888EFG",
        invalid_token,
    )
    assert status == 401
    assert body["codigo"] == "CREDENCIALES_INVALIDAS"


def _invoice_items(table, paso_id):
    items = []
    scan_kwargs = {
        "FilterExpression": Attr("paso_id").eq(paso_id),
        "ConsistentRead": True,
    }

    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))

        if "LastEvaluatedKey" not in response:
            return items

        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _invoke_consumer(function_name, event):
    payload = {
        "Records": [{
            "messageId": "integration-registry-idempotency",
            "body": json.dumps(event),
        }]
    }

    lambda_client = boto3.client("lambda", region_name="us-east-1")
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )

    result = json.loads(response["Payload"].read())
    assert result.get("batchItemFailures") == []
    return result


@INTEGRATION
def test_registry_consumer_is_idempotent():
    outputs = _outputs(MAIN_STACK)
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

    pasos = dynamodb.Table(outputs["PasosTableName"])
    facturas = dynamodb.Table(outputs["FacturasTableName"])

    paso = pasos.get_item(
        Key={"paso_id": IDEMPOTENCY_PASO_ID},
        ConsistentRead=True,
    ).get("Item")

    assert paso is not None
    assert paso["estado"] in {"NO_REGISTRADO", "FACTURADO"}

    occurred_at = datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")

    event = make_event(
        "paso.no_registrado",
        paso["paso_id"],
        int(paso["version"]),
        {
            "paso_id": paso["paso_id"],
            "placa": paso["placa"],
            "peaje_id": paso["peaje_id"],
        },
        "integration:" + paso["paso_id"],
        occurred_at,
        paso.get("carnet", "20230223"),
    )

    _invoke_consumer(
        outputs["RegistryConsumerFunctionName"],
        event,
    )

    first = pasos.get_item(
        Key={"paso_id": IDEMPOTENCY_PASO_ID},
        ConsistentRead=True,
    )["Item"]

    assert first["estado"] == "FACTURADO"

    invoices_after_first = _invoice_items(
        facturas,
        IDEMPOTENCY_PASO_ID,
    )

    assert len(invoices_after_first) == 1

    _invoke_consumer(
        outputs["RegistryConsumerFunctionName"],
        event,
    )

    second = pasos.get_item(
        Key={"paso_id": IDEMPOTENCY_PASO_ID},
        ConsistentRead=True,
    )["Item"]

    invoices_after_second = _invoice_items(
        facturas,
        IDEMPOTENCY_PASO_ID,
    )

    assert second["estado"] == "FACTURADO"
    assert second["factura_id"] == first["factura_id"]
    assert second["version"] == first["version"]
    assert len(invoices_after_second) == 1
