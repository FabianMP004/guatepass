# GuatePass — catálogo de eventos v2.1.0

Esta versión adopta los source y detail-type de la tabla de tarea 6. Es un cambio
incompatible de nombres: sobres nuevos llevan schema_version=2. El catálogo v1
se conserva para leer registros existentes. Revisión del equipo requerida en PR.

## Sobre común

source y detail-type se corresponden exactamente con la tabla siguiente.
detail incluye grupo=guatepass, carnet derivado por servidor, schema_version,
evento_id, aggregate_id, aggregate_version positivo, request_id y occurred_at UTC.
evento_id sigue siendo stable_id('evento', detail-type, aggregate_id, version).
No usar el id asignado por EventBridge para deduplicar.

## Catálogo y conexiones

| source | detail-type | Destinos de negocio | Conexiones activas tras tarea 8 |
|---|---|---|---|
| guatepass.pasos | paso.registrado | cobro y tiempo real | cola-pasos |
| guatepass.pasos | paso.cobrado | notificación, tiempo real, cierre de espera | cola-notificaciones |
| guatepass.pasos | paso.pendiente | inicio de espera, notificación, tiempo real, reintento de drenaje | cola-pasos, cola-notificaciones |
| guatepass.pasos | paso.no_registrado | registro y tiempo real | cola-registro |
| guatepass.pasos | paso.no_identificado | tiempo real | pendiente del consumidor de tiempo real |
| guatepass.pasos | paso.facturado | notificación y tiempo real | cola-notificaciones |
| guatepass.registro | invitacion.solicitada | notificación | cola-notificaciones |
| guatepass.saldo | saldo.recargado | cobro de pendientes y notificación | cola-pasos, cola-notificaciones |
| guatepass.saldo | saldo.devuelto | cobro de pendientes y notificación | cola-pasos, cola-notificaciones |
| guatepass.disputas | disputa.abierta | inicio de espera y notificación | cola-notificaciones |
| guatepass.disputas | disputa.resuelta | cierre de espera y notificación | cola-notificaciones |
| guatepass.recaudacion | recaudacion.calculada | finanzas | cola-notificaciones, destinatario finanzas |

Las conexiones de tiempo real y esperas no están implementadas por este paquete.
Añadir sus reglas/targets y DLQ cuando existan sus consumidores (tareas 8–10 y 12).
Tarea 8 conecta CobrarFunction a paso.registrado, paso.pendiente, saldo.recargado y
saldo.devuelto. El drenaje lee la primera deuda de Pendientes con consistencia fuerte;
una continuación técnica en SQS usa una marca recuperable en Esperas y una regla
programada la vuelve a enviar si falla el envío inicial. No es un evento de negocio
ni entra al Archive. Las deudas vencidas permanecen hasta su facturación futura.
Cola-registro sigue sin consumidor hasta la tarea del externo.

## Snapshot requerido por transición

| Evento | Campos de detail además del sobre |
|---|---|
| paso.registrado | paso_id, placa, peaje_id, timestamp, tag_id nullable; recibido_en generado por webhook |
| paso.cobrado | paso_id, cobro_id, placa, peaje_id, monto_centavos, cobrado_en |
| paso.pendiente | paso_id, placa, peaje_id, monto_centavos, vence_en |
| paso.no_registrado / paso.no_identificado | paso_id, placa, peaje_id |
| paso.facturado | paso_id, factura_id, placa, peaje_id, monto_centavos, concepto |
| invitacion.solicitada | paso_id, placa, contacto con email y/o telefono como texto |
| saldo.recargado | recarga_id, placa, monto_centavos |
| saldo.devuelto | disputa_id, devolucion_id, cobro_id, placa, peaje_id, monto_centavos, devuelto_en, resolucion |
| disputa.abierta | disputa_id, cobro_id, placa, vence_en |
| disputa.resuelta | disputa_id, cobro_id, placa, decision APROBAR/RECHAZAR, motivo |
| recaudacion.calculada | reporte_id, fecha local YYYY-MM-DD, calculado_en |

Montos: enteros positivos de centavos (bool no válido). saldo_centavos opcional:
entero no negativo y snapshot del instante de transición. El consumidor no lee
el saldo actual para reconstruir el texto. La notificación de reporte indica
su identificador y fecha, no consulta ni reconstruye totales actuales.

APROBAR requiere confirmar la devolución en el flujo de negocio; emitir
disputa.resuelta y saldo.devuelto como hechos distintos con Outbox transaccional.
Un cierre de espera no produce otra devolución por sí solo.

Invitación: excepción explícita al catálogo histórico que prohibía contacto en
bus. El externo aún no tiene almacén de referencias de contacto accesible al
notificador; este contrato transporta el contacto mínimo de la respuesta externa
en el snapshot para poder simular email/SMS. No incluir NIT ni otros datos.
Archive retiene este snapshot 30 días, protegido por IAM y cifrado. Revisar esta
decisión en el PR; si se requiere no transportar contacto, implementar primero
un contact_ref y su almacén protegido, cambiando productor y consumidor juntos.
El notificador no imprime ni almacena el contacto bruto en su registro.

## Notificaciones

notificacion_id = evento_id + '#' + destinatario.
destinatario: placa:PLACA para usuario/invitación o finanzas para reporte.
Canales: EMAIL_SIMULADO, SMS_SIMULADO o PORTAL_SIMULADO. Sin envío externo.
Invitación con teléfono y sin email selecciona SMS_SIMULADO.
La tabla guarda texto, destinatario lógico, canal, fecha de registro, occurred_at,
evento_id y hash del contenido. Misma clave con otro contenido es error y se
reintenta hasta DLQ: no sobrescribir silenciosamente el mensaje anterior.
Para usuarios conocidos se consulta solo el contacto, nunca el saldo. Si no hay
contacto, el registro se conserva como PORTAL_SIMULADO. Un replay conserva la
fecha y texto del primer registro. Metadatos de EventBridge no intervienen en hash.

## Compatibilidad y migración

No convertir registros v1 almacenados en Outbox ni editar Pasos existentes.
El publicador entiende v1 y v2 y conserva el sobre original en cada reintento.
El webhook produce v2 solo para pasos nuevos. Un replay HTTP de un paso v1
devuelve su ID original y no emite otra transición.

| v1 (source=guatepass) | Interpretación del consumidor |
|---|---|
| PasoRegistrado | paso.registrado |
| CobroRealizado | paso.cobrado |
| PasoPendiente | paso.pendiente |
| PasoNoRegistrado | paso.no_registrado |
| PasoNoIdentificado | paso.no_identificado |
| FacturaEmitida | paso.facturado |
| RecargaAplicada | saldo.recargado |
| DevolucionRealizada | saldo.devuelto |
| DisputaAbierta | disputa.abierta |
| DisputaRechazada | disputa.resuelta, decisión RECHAZAR |
| RecaudacionCalculada | recaudacion.calculada |

La interpretación no reescribe evento_id. No publicar simultáneamente v1 y v2
para la misma transición: tendrían IDs diferentes. TagAsociado/TagDesactivado
siguen siendo v1 de auditoría, sin reglas nuevas en esta tarea.
DevolucionRealizada no se convierte automáticamente en dos eventos.

## Retención, errores y replay

- Colas principales: 4 días, visibility timeout 180 s, maxReceiveCount=5.
- Notificar: timeout 30 s, ventana lote 0, lote 10, ReportBatchItemFailures.
  180 = 6 x 30 + 0. Si cambia timeout/ventana de un futuro consumidor, revisar cola.
- DLQ de cada consumidor: 14 días; redrive permitido solo desde su cola.
- DLQ compartida de entregas EventBridge: 14 días, restringida a las seis reglas.
  Cada target reintenta hasta 10 veces o 3600 s, lo que ocurra primero.
- Archive: 30 días para source guatepass y sus cinco dominios v2.
- Logs: 14 días. Outbox y notificaciones sin TTL durante el proyecto.

Un error EventBridge -> SQS va a RoutingDeliveryDLQ; un error del handler SQS
va a NotificacionesDLQ tras redrive. No confundirlas con OutboxDLQ del Stream.
No purgar DLQ. Revisar, corregir consumidor o evento y luego reintroducir con ID
original; el proceso de redrive no autoriza cambiar hechos del negocio.

Archive solo contiene eventos que llegaron al bus después de crearlo. Esperar
al menos diez minutos antes de replays recientes. Se reenvían al mismo bus,
con replay-name y sin orden garantizado. Outbox recupera eventos que nunca llegaron.
Antes de replay: consumidores idempotentes, intervalo UTC acotado y reglas de
destino específicas. Nunca replay global de cobros sin probar idempotencia.

Ejemplo Python (solo ejecutar al autorizar ese replay, no parte de despliegue):

```python
events.start_replay(
    ReplayName='demo-notificaciones-IDENTIFICADOR_UNICO',
    EventSourceArn=outputs['GuatePassArchiveArn'],
    EventStartTime=inicio_utc,
    EventEndTime=fin_utc,
    Destination={
        'Arn': outputs['EventBusArn'],
        'FilterArns': [outputs['NotificacionesRuleArn'],
                       outputs['NotificacionesLegacyRuleArn']],
    },
)
```

Monitorear describe_replay y registros de notificaciones. No esperar cambios de
saldo por replay dirigido solo a notificaciones.

## Adiciones compatibles de tarea 7

Los eventos de paso incluyen modalidad y monto_centavos fijados al clasificar.
COBRADO y PENDIENTE incluyen saldo_centavos capturado en su transacción.
La clasificación incrementa version sin emitir un evento público: las versiones
son monótonas, no necesariamente consecutivas en el bus. schema_version sigue 2.
TagAsociado/TagDesactivado conservan el contrato v1, source guatepass, agregado
usuario:PLACA y versión del usuario; contienen placa, tag_id y activo. Se guardan
en Outbox y Archive; esta tarea no añade notificaciones de tags.
