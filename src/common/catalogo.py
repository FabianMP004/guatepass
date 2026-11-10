"""Catalogue v2. Legacy envelopes remain immutable and readable."""
SOURCES = {
    'paso.registrado': 'guatepass.pasos',
    'paso.cobrado': 'guatepass.pasos',
    'paso.pendiente': 'guatepass.pasos',
    'paso.no_registrado': 'guatepass.pasos',
    'paso.no_identificado': 'guatepass.pasos',
    'paso.facturado': 'guatepass.pasos',
    'invitacion.solicitada': 'guatepass.registro',
    'saldo.recargado': 'guatepass.saldo',
    'saldo.devuelto': 'guatepass.saldo',
    'disputa.abierta': 'guatepass.disputas',
    'disputa.resuelta': 'guatepass.disputas',
    'recaudacion.calculada': 'guatepass.recaudacion',
}
LEGACY = {
    'PasoRegistrado': 'paso.registrado', 'CobroRealizado': 'paso.cobrado',
    'PasoPendiente': 'paso.pendiente', 'PasoNoRegistrado': 'paso.no_registrado',
    'PasoNoIdentificado': 'paso.no_identificado', 'FacturaEmitida': 'paso.facturado',
    'RecargaAplicada': 'saldo.recargado', 'DevolucionRealizada': 'saldo.devuelto',
    'DisputaAbierta': 'disputa.abierta', 'DisputaRechazada': 'disputa.resuelta',
    'RecaudacionCalculada': 'recaudacion.calculada',
}
NOTIFIABLE = {'paso.cobrado', 'paso.pendiente', 'paso.facturado',
    'invitacion.solicitada', 'saldo.recargado', 'saldo.devuelto',
    'disputa.abierta', 'disputa.resuelta', 'recaudacion.calculada'}
REQUIRED = {
    'paso.registrado': ('paso_id', 'placa', 'peaje_id', 'timestamp'),
    'paso.cobrado': ('paso_id', 'cobro_id', 'placa', 'peaje_id', 'cobrado_en'),
    'paso.pendiente': ('paso_id', 'placa', 'peaje_id', 'vence_en'),
    'paso.no_registrado': ('paso_id', 'placa', 'peaje_id'),
    'paso.no_identificado': ('paso_id', 'placa', 'peaje_id'),
    'paso.facturado': ('paso_id', 'factura_id', 'placa', 'peaje_id', 'concepto'),
    'invitacion.solicitada': ('paso_id', 'placa'),
    'saldo.recargado': ('recarga_id', 'placa'),
    'saldo.devuelto': ('disputa_id', 'devolucion_id', 'cobro_id', 'placa', 'peaje_id', 'devuelto_en', 'resolucion'),
    'disputa.abierta': ('disputa_id', 'cobro_id', 'placa', 'vence_en'),
    'disputa.resuelta': ('disputa_id', 'cobro_id', 'placa', 'decision', 'motivo'),
    'recaudacion.calculada': ('reporte_id', 'fecha', 'calculado_en'),
}
MONEY = {'paso.cobrado','paso.pendiente','paso.facturado','saldo.recargado','saldo.devuelto'}

def validate_payload(kind, detail):
    from datetime import date
    from common.models import utc_timestamp
    for field in REQUIRED[kind]:
        if not isinstance(detail.get(field), str) or not detail[field].strip():
            raise ValueError('MISSING_' + field.upper())
    if kind in MONEY:
        amount = detail.get('monto_centavos')
        if type(amount) is not int or amount <= 0:
            raise ValueError('INVALID_MONTO_CENTAVOS')
    if 'saldo_centavos' in detail and (type(detail['saldo_centavos']) is not int or detail['saldo_centavos'] < 0):
        raise ValueError('INVALID_SALDO_SNAPSHOT')
    for field in ('timestamp', 'cobrado_en', 'vence_en', 'devuelto_en', 'calculado_en'):
        if field in detail:
            utc_timestamp(detail[field])
    if kind == 'recaudacion.calculada':
        if date.fromisoformat(detail['fecha']).isoformat() != detail['fecha']:
            raise ValueError('INVALID_DATE')
    if kind == 'disputa.resuelta' and detail['decision'] not in ('APROBAR','RECHAZAR'):
        raise ValueError('INVALID_DECISION')
    if kind == 'paso.facturado' and detail['concepto'] not in ('PLAZO_VENCIDO','NO_REGISTRADO'):
        raise ValueError('INVALID_CONCEPTO')
    if kind == 'saldo.devuelto' and detail['resolucion'] not in ('MANUAL','AUTOMATICA'):
        raise ValueError('INVALID_RESOLUCION')
    if kind == 'paso.registrado':
        if 'tag_id' not in detail or (detail['tag_id'] is not None and
            (not isinstance(detail['tag_id'], str) or not detail['tag_id'].strip())):
            raise ValueError('INVALID_TAG')
    if kind == 'invitacion.solicitada':
        contact = detail.get('contacto')
        if not isinstance(contact, dict) or not any(
            isinstance(contact.get(k), str) and contact[k].strip() for k in ('email','telefono')):
            raise ValueError('CONTACT_REQUIRED')
        if set(contact) - {'email','telefono'}:
            raise ValueError('INVALID_CONTACT_FIELDS')
        if any(v is not None and not isinstance(v,str) for v in contact.values()):
            raise ValueError('INVALID_CONTACT_TYPE')
