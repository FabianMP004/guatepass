from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError

from common import dynamo as d
from common.catalogo import LEGACY
from common.events import outbox_item
from common.models import make_event, stable_id, validate_event


logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGISTRY_API_URL = os.environ.get("REGISTRY_API_URL", "")
REGISTRY_SECRET_ARN = os.environ.get("REGISTRY_SECRET_ARN", "")
REGISTRY_TIMEOUT_SECONDS = float(
    os.environ.get("REGISTRY_TIMEOUT_SECONDS", "3")
)

PREMIUM_AMOUNT_CENTAVOS = 750
PREMIUM_CONCEPT = "NO_REGISTRADO"
PLATE_PATTERN = re.compile(r"^[A-Z]-?\d{3}[A-Z]{3}$")

_secrets = boto3.client("secretsmanager")
_credentials_cache = None


class RegistryUnavailable(RuntimeError):
    pass


class RegistryCredentialsError(RuntimeError):
    pass


class RegistryOperationalError(RuntimeError):
    pass


def transact(db, operations):
    return db.transact_write_items(TransactItems=operations)


def _timestamp():
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _credentials():
    global _credentials_cache

    if _credentials_cache is not None:
        return _credentials_cache

    if not REGISTRY_SECRET_ARN:
        raise RegistryOperationalError("REGISTRY_SECRET_ARN_MISSING")

    try:
        response = _secrets.get_secret_value(
            SecretId=REGISTRY_SECRET_ARN
        )

        if "SecretString" in response:
            raw = response["SecretString"]
        else:
            binary = response["SecretBinary"]
            if isinstance(binary, str):
                binary = base64.b64decode(binary)
            raw = binary.decode("utf-8")

        value = json.loads(raw)
        username = value.get("username")
        password = value.get("password")

        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("INVALID_SECRET_FORMAT")

        _credentials_cache = username, password
        return _credentials_cache

    except Exception as exc:
        logger.error(
            "registry credential loading failed error_type=%s",
            type(exc).__name__,
        )
        raise RegistryOperationalError(
            "REGISTRY_CREDENTIALS_UNAVAILABLE"
        ) from exc


def _api_request(placa):
    if not REGISTRY_API_URL:
        raise RegistryOperationalError("REGISTRY_API_URL_MISSING")

    username, password = _credentials()
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    url = (
        REGISTRY_API_URL.rstrip("/")
        + "/registro/"
        + quote(placa, safe="")
    )

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=REGISTRY_TIMEOUT_SECONDS,
        ) as response:
            status = response.status
            body = response.read()

    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as exc:
        raise RegistryUnavailable(
            "REGISTRY_TIMEOUT_OR_NETWORK_ERROR"
        ) from exc

    if status == 404:
        return None

    if status in (401, 403):
        raise RegistryCredentialsError(
            "REGISTRY_CREDENTIALS_REJECTED"
        )

    if status >= 500:
        raise RegistryUnavailable(
            f"REGISTRY_HTTP_{status}"
        )

    if status != 200:
        raise RegistryOperationalError(
            f"REGISTRY_HTTP_{status}"
        )

    try:
        value = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise RegistryOperationalError(
            "REGISTRY_INVALID_JSON"
        ) from exc

    if not isinstance(value, dict):
        raise RegistryOperationalError(
            "REGISTRY_INVALID_RESPONSE"
        )

    return value


def consultar_registro(placa: str) -> dict | None:
    if not isinstance(placa, str):
        raise ValueError("INVALID_PLACA")

    placa = placa.strip().upper()

    if not PLATE_PATTERN.fullmatch(placa):
        raise ValueError("INVALID_PLACA")

    result = _api_request(placa)

    if result is None:
        return None

    if result.get("placa") != placa:
        raise RegistryOperationalError(
            "REGISTRY_PLATE_MISMATCH"
        )

    propietario = result.get("propietario")
    nit = result.get("nit")

    if (
        not isinstance(propietario, str)
        or not propietario.strip()
        or not isinstance(nit, str)
        or not nit.strip()
    ):
        raise RegistryOperationalError(
            "REGISTRY_FISCAL_DATA_MISSING"
        )

    contact = {}

    for field in ("email", "telefono"):
        value = result.get(field)
        if value is not None:
            if not isinstance(value, str):
                raise RegistryOperationalError(
                    "REGISTRY_CONTACT_INVALID"
                )
            if value.strip():
                contact[field] = value.strip()

    return {
        "placa": placa,
        "propietario": propietario.strip(),
        "nit": nit.strip(),
        "contacto": contact,
    }


def _pass_payload(paso):
    return {
        key: paso[key]
        for key in (
            "paso_id",
            "placa",
            "peaje_id",
            "modalidad",
            "fecha_local",
        )
        if key in paso
    }


def _mark_not_identified(db, paso):
    current = datetime.now(timezone.utc)
    occurred_at = current.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")

    final = dict(
        paso,
        estado="NO_IDENTIFICADO",
        version=paso["version"] + 1,
        identificado_en=occurred_at,
    )

    event = make_event(
        "paso.no_identificado",
        paso["paso_id"],
        final["version"],
        _pass_payload(final),
        "registro:" + paso["paso_id"],
        occurred_at,
        paso.get("carnet") or os.environ["OWNER_CARNET"],
    )

    transact(
        db,
        [
            d.put(
                "PASOS",
                final,
                paso,
                ["version", "estado"],
            ),
            d.put("OUTBOX", outbox_item(event)),
        ],
    )


def _invoice_premium(db, paso, registry):
    current = datetime.now(timezone.utc)
    issued_at = current.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")

    factura_id = stable_id(
        "factura",
        paso["paso_id"],
        PREMIUM_CONCEPT,
    )

    local_date = paso.get("fecha_local") or current.date().isoformat()
    propietario = registry["propietario"]
    nit = registry["nit"]

    invoice = {
        "factura_id": factura_id,
        "paso_id": paso["paso_id"],
        "placa": paso["placa"],
        "peaje_id": paso["peaje_id"],
        "monto_centavos": PREMIUM_AMOUNT_CENTAVOS,
        "tarifa_centavos": PREMIUM_AMOUNT_CENTAVOS,
        "concepto": PREMIUM_CONCEPT,
        "numero": "GP-" + factura_id,
        "fecha": local_date,
        "emitida_en": issued_at,
        "orden": issued_at + "#" + factura_id,
        "peaje_fecha": paso["peaje_id"] + "#" + local_date,
        "NIT": nit,
        "nombre": propietario,
        "estado": "POR_COBRAR",
        "contribuyente": {
            "nombre": propietario,
            "nit": nit,
        },
    }

    final = dict(
        paso,
        estado="FACTURADO",
        factura_id=factura_id,
        version=paso["version"] + 1,
        facturado_en=issued_at,
    )

    payload = dict(
        _pass_payload(final),
        factura_id=factura_id,
        concepto=PREMIUM_CONCEPT,
        monto_centavos=PREMIUM_AMOUNT_CENTAVOS,
        facturado_en=issued_at,
    )

    carnet = paso.get("carnet") or os.environ["OWNER_CARNET"]

    invoice_event = make_event(
        "paso.facturado",
        paso["paso_id"],
        final["version"],
        payload,
        "registro:" + paso["paso_id"],
        issued_at,
        carnet,
    )

    operations = [
        d.put(
            "PASOS",
            final,
            paso,
            ["version", "estado"],
        ),
        d.put("FACTURAS", invoice),
        d.put("OUTBOX", outbox_item(invoice_event)),
    ]

    contact = registry.get("contacto") or {}

    if contact.get("email") or contact.get("telefono"):
        invitation_event = make_event(
            "invitacion.solicitada",
            paso["paso_id"],
            final["version"],
            {
                "paso_id": paso["paso_id"],
                "placa": paso["placa"],
                "contacto": {
                    "email": contact.get("email"),
                    "telefono": contact.get("telefono"),
                },
            },
            "registro:" + paso["paso_id"],
            issued_at,
            carnet,
        )
        operations.append(
            d.put("OUTBOX", outbox_item(invitation_event))
        )

    transact(db, operations)


def process_pass(paso_id):
    db = d.client()
    paso = d.get(db, "PASOS", {"paso_id": paso_id})

    if not paso:
        return "PASS_NOT_FOUND"

    if paso["estado"] in {
        "FACTURADO",
        "NO_IDENTIFICADO",
    }:
        return "ALREADY_FINISHED"

    if paso["estado"] != "NO_REGISTRADO":
        raise ValueError("INVALID_REGISTRY_PASS_STATE")

    registry = consultar_registro(paso["placa"])

    if registry is None:
        _mark_not_identified(db, paso)
        return "NO_IDENTIFICADO"

    _invoice_premium(db, paso, registry)
    return "FACTURADO"


def handler(event, context):
    failures = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            validate_event(message)

            kind = LEGACY.get(
                message["detail-type"],
                message["detail-type"],
            )

            if kind != "paso.no_registrado":
                raise ValueError("UNSUPPORTED_REGISTRY_EVENT")

            process_pass(message["detail"]["paso_id"])

        except Exception as exc:
            aws_code = None
            aws_message = None
            cancellation_codes = None

            if isinstance(exc, ClientError):
                error = exc.response.get("Error", {})
                aws_code = error.get("Code")
                aws_message = error.get("Message")
                cancellation_codes = [
                    item.get("Code")
                    for item in exc.response.get(
                        "CancellationReasons",
                        [],
                    )
                ]

            logger.error(
                "registry consumer failed message_id=%s "
                "error_type=%s aws_code=%s aws_message=%s "
                "cancellation_codes=%s",
                record.get("messageId"),
                type(exc).__name__,
                aws_code,
                aws_message,
                cancellation_codes,
            )

            failures.append(
                {"itemIdentifier": record["messageId"]}
            )

    return {"batchItemFailures": failures}
