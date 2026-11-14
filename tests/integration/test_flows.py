"""Verificación de evidencia real de la ejecución de tarea 15.

Estas pruebas son opt-in y de solo lectura. No envían webhooks, no recargan
saldos y no modifican tablas.
"""
import os

import boto3
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FLOWS_AWS") != "1",
    reason="Requiere RUN_FLOWS_AWS=1 y el stack dev desplegado",
)


def _context():
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    stack_name = os.getenv("GP_STACK", "guatepass-dev")
    session = boto3.Session(region_name=region)
    stack = session.client("cloudformation").describe_stacks(
        StackName=stack_name
    )["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    return outputs, session.resource("dynamodb")


def _scan(table, predicate):
    rows = []
    kwargs = {"ConsistentRead": True}
    while True:
        page = table.scan(**kwargs)
        rows.extend(row for row in page.get("Items", []) if predicate(row))
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _pass(outputs, db, paso_id):
    return db.Table(outputs["PasosTableName"]).get_item(
        Key={"paso_id": paso_id},
        ConsistentRead=True,
    ).get("Item")


def _movements(outputs, db, predicate):
    return _scan(db.Table(outputs["MovimientosTableName"]), predicate)


def _invoices(outputs, db, paso_id):
    return _scan(
        db.Table(outputs["FacturasTableName"]),
        lambda row: row.get("paso_id") == paso_id,
    )


def _notifications(outputs, db, placa):
    return _scan(
        db.Table(outputs["NotificacionesTableName"]),
        lambda row: row.get("destinatario") == f"placa:{placa}",
    )


def _dispute(outputs, db, disputa_id):
    rows = _scan(
        db.Table(outputs["DisputasTableName"]),
        lambda row: row.get("disputa_id") == disputa_id,
    )
    assert len(rows) == 1
    return rows[0]


def test_f1_tag_real_evidence():
    outputs, db = _context()
    paso_id = "paso_0fc46bf244907216653b6526d652ed9226cb3343031bdba93ea7127c2b1460f7"
    paso = _pass(outputs, db, paso_id)

    assert paso["estado"] == "COBRADO"
    assert paso["modalidad"] == "TAG"
    assert paso["tag_id"] == "TAG-005"
    assert int(paso["monto_centavos"]) == 450

    movimientos = _movements(
        outputs, db, lambda row: row.get("paso_id") == paso_id
    )
    assert len(movimientos) == 1
    assert movimientos[0]["tipo"] == "COBRO"
    assert int(movimientos[0]["monto_centavos"]) == 450


def test_f1_recarga_real_evidence():
    outputs, db = _context()
    paso_id = "paso_200eccfb969d2c74b06cad87a2ab186bc35af37f0bd0be179cf7c459d93d705a"
    paso = _pass(outputs, db, paso_id)

    assert paso["estado"] == "COBRADO"
    assert paso["modalidad"] == "TAG"
    assert paso["tag_id"] == "TAG-003"
    assert int(paso["monto_centavos"]) == 450
    assert len(
        _movements(outputs, db, lambda row: row.get("paso_id") == paso_id)
    ) == 1


def test_f2_expired_deadline_real_evidence():
    outputs, db = _context()
    paso_id = "paso_4a4e671014e3a309e82b0bcd1d8fe7c78b06199bed4034d7716136fbe2567779"
    paso = _pass(outputs, db, paso_id)
    factura = _invoices(outputs, db, paso_id)

    assert paso["estado"] == "FACTURADO"
    assert int(paso["monto_centavos"]) == 450
    assert len(factura) == 1
    assert int(factura[0]["monto_centavos"]) == 2950
    assert factura[0]["concepto"] == "PLAZO_VENCIDO"
    assert int(factura[0]["recargo_centavos"]) == 2500


def test_f3_registry_positive_real_evidence():
    outputs, db = _context()
    paso_id = "paso_20eb36df324f5e79e5452454d5e49f0175e21f1fae74275b187f3e124aef9ec5"
    paso = _pass(outputs, db, paso_id)
    factura = _invoices(outputs, db, paso_id)
    notificaciones = _notifications(outputs, db, "P-888EFG")

    assert paso["estado"] == "FACTURADO"
    assert paso["modalidad"] == "NO_REGISTRADO"
    assert int(paso["monto_centavos"]) == 750
    assert len(factura) == 1
    assert factura[0]["NIT"] == "2234567-1"
    assert factura[0]["nombre"] == "Rosa Xitumul"
    assert any(
        row.get("tipo") == "invitacion.solicitada"
        for row in notificaciones
    )


def test_f3_registry_without_contact_real_evidence():
    outputs, db = _context()
    paso_id = "paso_563ea90ed1f742fd2c568d378c8bf82314c457335c8c60ab85aa2963da3ef263"
    paso = _pass(outputs, db, paso_id)
    factura = _invoices(outputs, db, paso_id)
    notificaciones = _notifications(outputs, db, "P-789GHI")

    assert paso["estado"] == "FACTURADO"
    assert paso["modalidad"] == "NO_REGISTRADO"
    assert int(paso["monto_centavos"]) == 750
    assert len(factura) == 1
    assert factura[0]["NIT"] == "3456789-3"
    assert not any(
        row.get("tipo") == "invitacion.solicitada"
        for row in notificaciones
    )


def test_f3_unknown_plate_real_evidence():
    outputs, db = _context()
    paso_id = "paso_30d33c5dd4dbeb03c8ae362d98087fbe288973053d55f88ff983da0a473df272"
    paso = _pass(outputs, db, paso_id)

    assert paso["estado"] == "NO_IDENTIFICADO"
    assert paso["modalidad"] == "NO_REGISTRADO"
    assert len(_invoices(outputs, db, paso_id)) == 0
    assert len(
        _movements(outputs, db, lambda row: row.get("paso_id") == paso_id)
    ) == 0


def test_f4_disputes_mixed_real_evidence():
    outputs, db = _context()
    rejected_id = "disputa_d00a2c8049c796e63bec553888c670a29fe90de409f34593bb804aacace1d3f9"
    approved_id = "disputa_6a1b1380b65f6debfd557338b4ef45c7588ed52eb0a718c3435f8f1007efe4b7"

    rejected = _dispute(outputs, db, rejected_id)
    approved = _dispute(outputs, db, approved_id)

    assert rejected["estado"] == "RECHAZADA"
    assert rejected["decision"] == "RECHAZAR"
    assert rejected.get("devolucion_id") is None

    assert approved["estado"] == "APROBADA"
    assert approved["decision"] == "APROBAR"
    assert approved["resolucion"] == "AUTOMATICA"
    assert approved["devolucion_id"] == "devolucion_56d0e5d5ccc2fbbcf748376cef324acc1cc2fc61d355c0f998ef078f65bff7e8"

    devoluciones = _movements(
        outputs,
        db,
        lambda row: (
            row.get("disputa_id") == approved_id
            and row.get("tipo") == "DEVOLUCION"
        ),
    )
    assert len(devoluciones) == 1
    assert int(devoluciones[0]["monto_centavos"]) == 450


def test_f5_app_debit_pending_real_evidence():
    outputs, db = _context()
    paid_id = "paso_bce125060c17077050858c4b5a2207e25c820334ea643932cc73a0b876679d27"
    pending_id = "paso_6609189491a5f975ba38cd862546090f1ca9ed26e58dc2f02e08135a84a8b3ea"

    paid = _pass(outputs, db, paid_id)
    pending = _pass(outputs, db, pending_id)

    assert paid["estado"] == "COBRADO"
    assert pending["modalidad"] == "APP"
    assert int(pending["monto_centavos"]) == 500
    assert pending.get("vence_en")
    assert len(
        _movements(outputs, db, lambda row: row.get("paso_id") == paid_id)
    ) == 1
    assert len(
        _movements(outputs, db, lambda row: row.get("paso_id") == pending_id)
    ) == 0


def test_f6_revenue_real_evidence():
    outputs, db = _context()
    report_id = "recaudacion_0b004c65f854afe9e79ad1b0a17e5a1a1d5ccd0d6b2e71d63ef21ac40467651c"
    report = db.Table(outputs["RecaudacionTableName"]).get_item(
        Key={"recaudacion_id": report_id},
        ConsistentRead=True,
    ).get("Item")

    assert report["fecha"] == "2026-11-14"
    assert report["peaje_id"] == "PEAJE_ZONA10"
    assert int(report["cobros_centavos"]) == 13700
    assert int(report["devoluciones_centavos"]) == 1350
    assert int(report["neto_centavos"]) == 12350
    assert int(report["por_cobrar_centavos"]) == 17850


def test_f7_duplicate_notice_real_evidence():
    outputs, db = _context()
    paso_id = "paso_30ab72ab171a95633034388b711f701c5139987e4f2edbe342583eff6c087b34"
    paso = _pass(outputs, db, paso_id)
    movimientos = _movements(
        outputs, db, lambda row: row.get("paso_id") == paso_id
    )
    maria = db.Table(outputs["UsuariosTableName"]).get_item(
        Key={"placa": "P-456DEF"},
        ConsistentRead=True,
    )["Item"]

    assert paso["estado"] == "COBRADO"
    assert paso["modalidad"] == "TAG"
    assert paso["tag_id"] == "TAG-001"
    assert int(paso["monto_centavos"]) == 450
    assert len(movimientos) == 1
    assert int(maria["saldo_centavos"]) == 24550
