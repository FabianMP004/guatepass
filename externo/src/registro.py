import base64
import binascii
import hmac
import json
import logging
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ["REGISTRY_TABLE"]
SECRET_ARN = os.environ["REGISTRY_SECRET_ARN"]
AVAILABILITY_PARAMETER = os.environ["REGISTRY_AVAILABILITY_PARAMETER"]

dynamodb = boto3.resource("dynamodb")
secrets = boto3.client("secretsmanager")
ssm = boto3.client("ssm")

PLATE_PATTERN = re.compile(r"^[A-Z]-?\d{3}[A-Z]{3}$")


def reply(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _headers(event):
    return {
        str(key).lower(): value
        for key, value in (event.get("headers") or {}).items()
        if value is not None
    }


def _credentials(event):
    headers = _headers(event)
    authorization = headers.get("authorization")

    if authorization:
        scheme, _, encoded = authorization.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return None, None

        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            username, separator, password = decoded.partition(":")
            if not separator:
                return None, None
            return username, password
        except (ValueError, UnicodeError, binascii.Error):
            return None, None

    username = (
        headers.get("x-registro-username")
        or headers.get("x-registry-username")
    )
    password = (
        headers.get("x-registro-password")
        or headers.get("x-registry-password")
    )
    return username, password


def _secret_credentials():
    response = secrets.get_secret_value(SecretId=SECRET_ARN)

    if "SecretString" in response:
        raw = response["SecretString"]
    else:
        raw = base64.b64decode(response["SecretBinary"]).decode("utf-8")

    value = json.loads(raw)
    username = value.get("username")
    password = value.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("INVALID_REGISTRY_SECRET")

    return username, password


def _is_available():
    response = ssm.get_parameter(Name=AVAILABILITY_PARAMETER)
    value = str(response["Parameter"]["Value"]).strip().lower()
    return value in {"on", "true", "1", "available", "up"}


def _valid_credentials(event):
    supplied_username, supplied_password = _credentials(event)

    if not isinstance(supplied_username, str):
        return False
    if not isinstance(supplied_password, str):
        return False

    expected_username, expected_password = _secret_credentials()

    return (
        hmac.compare_digest(supplied_username, expected_username)
        and hmac.compare_digest(supplied_password, expected_password)
    )


def _plate(event):
    path_parameters = event.get("pathParameters") or {}
    value = path_parameters.get("placa")

    if not isinstance(value, str):
        return None

    value = value.strip().upper()

    if not PLATE_PATTERN.fullmatch(value):
        return None

    return value


def handler(event, context):
    try:
        if not _is_available():
            return reply(
                503,
                {
                    "codigo": "REGISTRO_NO_DISPONIBLE",
                    "mensaje": "Registro externo temporalmente no disponible.",
                },
            )

        if not _valid_credentials(event):
            return reply(
                401,
                {
                    "codigo": "CREDENCIALES_INVALIDAS",
                    "mensaje": "Credenciales inválidas.",
                },
            )

        placa = _plate(event)

        if not placa:
            return reply(
                400,
                {
                    "codigo": "PLACA_INVALIDA",
                    "mensaje": "La placa no tiene un formato válido.",
                },
            )

        item = dynamodb.Table(TABLE_NAME).get_item(
            Key={"placa": placa},
            ConsistentRead=True,
        ).get("Item")

        if item is None:
            return reply(
                404,
                {
                    "codigo": "PLACA_NO_ENCONTRADA",
                    "mensaje": "La placa no existe en el registro externo.",
                    "placa": placa,
                },
            )

        return reply(
            200,
            {
                "placa": placa,
                "propietario": item.get("propietario"),
                "nit": item.get("nit"),
                "email": item.get("email") or None,
                "telefono": item.get("telefono") or None,
            },
        )

    except (ClientError, BotoCoreError):
        logger.exception("registry dependency unavailable")
        return reply(
            503,
            {
                "codigo": "REGISTRO_NO_DISPONIBLE",
                "mensaje": "Registro externo temporalmente no disponible.",
            },
        )
    except Exception:
        logger.exception("registry request failed")
        return reply(
            500,
            {
                "codigo": "ERROR_REGISTRO",
                "mensaje": "Error interno del registro externo.",
            },
        )
