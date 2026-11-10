# GuatePass — catálogo de eventos v1.0.0

> HISTÓRICO desde tarea 6. Para productores nuevos usar `eventos-v2.md`.
> Los registros v1 existentes se publican sin convertir source, detail-type,
> schema_version ni evento_id. Las reglas Legacy permiten procesarlos.

Contrato propuesto para revisión conjunta. source siempre guatepass; schema_version=1.
Los eventos son hechos confirmados después de persistir. Publicación recuperable desde outbox/stream; no asumir atomicidad entre escribir DynamoDB y llamar EventBridge. El productor debe revisar fallos parciales y reintentar el mismo evento. Tono implementará transporte, colas y DLQ en tareas posteriores.

## Sobre lógico

```json
{
  "source": "guatepass",
  "detail-type": "PasoRegistrado",
  "detail": {
    "schema_version": 1,
    "grupo": "guatepass",
    "carnet": "OWNER_CARNET_CONFIGURADO",
    "evento_id": "evento_HASH_CALCULADO",
    "aggregate_id": "paso_HASH_CALCULADO",
    "aggregate_version": 1,
    "request_id": "req-ejemplo",
    "occurred_at": "2026-11-06T12:00:00.000000Z",
    "paso_id": "paso_HASH_CALCULADO",
    "placa": "P123ABC",
    "peaje_id": "T1",
    "timestamp": "2026-11-06T12:00:00.000000Z",
    "tag_id": null
  }
}
```

Los HASH del ejemplo son ilustrativos: construir con make_event para obtener IDs válidos.
El adapter PutEvents mapeará source→Source, detail-type→DetailType y detail→Detail (JSON serializado), junto con EventBusName del despliegue. El id asignado por EventBridge no reemplaza detail.evento_id.

Campos comunes obligatorios: los ocho mostrados en detail antes de paso_id. aggregate_version es entero positivo del agregado y se persiste, nunca contador local de Lambda. occurred_at es instante UTC de la transición persistida. request_id correlaciona la causa original. Los eventos asociados a un paso llevan además paso_id; los de recarga no inventan un paso.

## Carné y confianza

- Backend obtiene sub de identidad previamente validada; busca la asignación del usuario en datos derivados de integrantes.csv.
- Usuario asignado: carné de su integrante. Usuario sin asignar: OwnerCarnet.
- Cámara, ejecución programada, vencimiento automático: OwnerCarnet. Una ejecución manual de infraestructura también usa OwnerCarnet si no existe identidad de aplicación asignada.
- Eventos derivados inmediatos de una acción conservan su atribución persistida. Un vencimiento posterior es una nueva acción automática, usa OwnerCarnet aunque la disputa original tuviera otro carné.
- Nunca leer carnet, grupo, rol o sub del body como autoridad. make_event sobrescribe metadatos reservados. El validador comprueba formato; la autenticidad requiere permisos de publicación y procedencia confiable.
- No publicar tokens JWT, contraseñas, claves, task tokens de Step Functions, NIT ni contacto en el bus. Consumidores de notificación buscan datos autorizados por ID.

## Catálogo

| detail-type | Agregado | Productor | Consumidores | Campos específicos requeridos |
|---|---|---|---|---|
| PasoRegistrado | paso | webhook | enrutador de cobro, tiempo real | paso_id, placa, peaje_id, timestamp, tag_id (nullable) |
| PasoPendiente | paso | cobros | espera de recarga, notificaciones, tiempo real | paso_id, placa, peaje_id, monto_centavos, vence_en |
| PasoNoRegistrado | paso | clasificador | cola de registro externo, tiempo real | paso_id, placa, peaje_id |
| PasoNoIdentificado | paso | registro externo | tiempo real | paso_id, placa, peaje_id |
| CobroRealizado | paso | cobros | notificaciones, tiempo real | paso_id, cobro_id, placa, peaje_id, monto_centavos, cobrado_en |
| RecargaAplicada | recarga | recargas | procesador de pendientes FIFO por usuario, notificaciones | recarga_id, placa, monto_centavos |
| FacturaEmitida | paso | facturación | notificaciones, tiempo real | paso_id, factura_id, placa, peaje_id, monto_centavos, concepto |
| TagAsociado | tag | tags | auditoría | tag_id, placa |
| TagDesactivado | tag | tags | auditoría | tag_id, placa |
| DisputaAbierta | disputa | disputas | espera de resolución, notificaciones | disputa_id, cobro_id, placa, vence_en |
| DisputaRechazada | disputa | resolución | notificaciones | disputa_id, cobro_id, placa, motivo |
| DevolucionRealizada | disputa | resolución o vencimiento | notificaciones, historial, procesador de pendientes | disputa_id, devolucion_id, cobro_id, placa, peaje_id, monto_centavos, devuelto_en, resolucion (MANUAL/AUTOMATICA) |
| RecaudacionCalculada | reporte | tarea diaria/manual | notificaciones finanzas | reporte_id, fecha, calculado_en |

IDs/placas/peajes/motivos: cadenas no vacías; timestamps: RFC3339 con zona; monto_centavos: entero positivo (bool no válido); tag_id nullable sólo donde se indica. resolucion y concepto usan enums de API. Los campos específicos del catálogo deberán validarse en cada handler; validate_event del paquete valida el sobre y su ID, no todos los payloads de negocio.

## Entrega y orden

Consumidores toleran entrega repetida y fuera de orden. Deducir un efecto por evento_id y registrar su procesamiento con la modificación correspondiente; no marcar completado antes del efecto. Saldos, facturas y devoluciones también tienen claves de negocio únicas. Ignorar versiones viejas para proyecciones de estado, pero no descartar movimientos contables nuevos por tener distinta secuencia de entrega. Mantener cobros/devoluciones como movimientos separados.
La recarga despierta procesamiento de pendientes por orden de llegada persistido (secuencia y paso_id de desempate); una notificación no garantiza orden FIFO. Serializar por usuario o usar transacciones/condiciones. No basar exclusión en el orden del bus.
Resolución de disputa compite con timeout mediante condición atómica; una sola devolución. La aprobación y la devolución forman una única unidad de negocio: se publica DevolucionRealizada tras confirmar ambas. El rechazo no genera devolución.
Recaudación se obtiene de movimientos persistidos y fechas locales, no sumando a ciegas eventos recibidos. Una recalculación del mismo reporte aumenta aggregate_version.

## Revisión

Tono confirma source/detail-type y reglas; Cynthia confirma cuándo existe cada hecho; Fabián confirma IDs, carné y transacciones. Cambios incompatibles requieren nueva versión coordinada.
