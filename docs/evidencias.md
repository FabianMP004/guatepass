# Evidencias de la tarea 15

## Entorno

- Fecha local: `2026-11-14`
- Región AWS: `us-east-1`
- Stack: `guatepass-dev`
- Branch: `tono/eventos`
- Portal: `https://d3j87in9jsupwd.cloudfront.net`
- API: `https://vyahh3bpje.execute-api.us-east-1.amazonaws.com/dev`
- Timeout de recarga: 120 segundos
- Timeout de disputa: 120 segundos

Las pruebas se ejecutaron sobre AWS y se conservaron los registros generados. No se restauraron saldos ni se modificaron los CSV originales.

## Estado inicial de la secuencia completa

| Usuario | Placa | Saldo inicial |
|---|---|---:|
| Juan Pérez | `P-123ABC` | Q16.50 |
| Andrés Castillo | `P-666YZA` | Q1.50 |
| María López | `P-456DEF` | Q225.00 |
| Diego Ramírez | `P-444STU` | Q5.00 |

Los valores corresponden al inicio de la secuencia real. Las pruebas posteriores se documentan con sus saldos iniciales y finales para no ocultar movimientos de preparación.

## F1: cobro TAG de Juan

- Acción: asociar `TAG-005` desde el portal y enviar un paso en `PEAJE_ZONA10`.
- `paso_id`: `paso_0fc46bf244907216653b6526d652ed9226cb3343031bdba93ea7127c2b1460f7`
- Estado: `COBRADO`
- Modalidad: `TAG`
- Monto: Q4.50
- Saldo: Q16.50 → Q12.00
- Evidencia AWS: paso, movimiento `COBRO`, Outbox y portal de conductor/operador.
- Resultado: correcto.

## F1: recarga de Andrés

- Saldo antes del paso: Q1.50.
- Acción: paso con `TAG-003` en `PEAJE_ZONA10`.
- `paso_id`: `paso_200eccfb969d2c74b06cad87a2ab186bc35af37f0bd0be179cf7c459d93d705a`
- Estado inicial: `PENDIENTE`
- Recarga posterior: Q20.00.
- Estado final: `COBRADO`
- Monto cobrado: Q4.50
- Saldo final: Q17.00
- Evidencia AWS: paso, un movimiento de cobro, saldo y pendientes vacíos.
- Resultado: correcto.

## F2: vencimiento del plazo

Para preparar el saldo se registraron tres pasos adicionales de Andrés, cada uno por Q4.50. El saldo pasó de Q17.00 a Q3.50. No se restauró el saldo.

- `paso_id`: `paso_4a4e671014e3a309e82b0bcd1d8fe7c78b06199bed4034d7716136fbe2567779`
- Saldo al registrar el paso: Q3.50.
- Estado inicial: `PENDIENTE`
- Vencimiento: 120 segundos.
- Estado final: `FACTURADO`
- Factura: `factura_815aec28ecc1fbca60de96cb299638472063caab2ec5eab986a0f0e773c541be`
- Monto factura: Q29.50
- Tarifa: Q4.50
- Recargo: Q25.00
- Concepto: `PLAZO_VENCIDO`
- Saldo final: Q3.50
- Resultado: correcto; no hubo cobro adicional.

## F3: registro externo positivo

- Placa: `P-888EFG`
- Propietaria: Rosa Xitumul.
- `paso_id`: `paso_20eb36df324f5e79e5452454d5e49f0175e21f1fae74275b187f3e124aef9ec5`
- Estado: `FACTURADO`
- Modalidad: `NO_REGISTRADO`
- Monto: Q7.50
- Factura: `factura_f49a08c258b5de98b41418c39aabd82024f4f884c3b3b213ed0987a0e2aa7103`
- NIT: `2234567-1`
- Invitación: generada por `invitacion.solicitada`.
- Evidencia: factura en DynamoDB, notificación simulada y portal del operador.
- Resultado: correcto.

## F3: registro externo sin contacto

- Placa: `P-789GHI`
- Propietario: Carlos Ruiz.
- `paso_id`: `paso_563ea90ed1f742fd2c568d378c8bf82314c457335c8c60ab85aa2963da3ef263`
- Estado: `FACTURADO`
- Modalidad: `NO_REGISTRADO`
- Monto: Q7.50
- Factura: `factura_30d22ea48d8caa2b0ca26b06dbe4cc33e20d33a4a13a0a42e13beff8b853f564`
- NIT: `3456789-3`
- Invitación: no generada porque no existe teléfono ni correo.
- Resultado: correcto.

## F3: placa no identificada

- Placa: `P-000XXX`
- `paso_id`: `paso_30d33c5dd4dbeb03c8ae362d98087fbe288973053d55f88ff983da0a473df272`
- Estado: `NO_IDENTIFICADO`
- Modalidad: `NO_REGISTRADO`
- Facturas: ninguna.
- Movimientos: ninguno.
- Resultado: correcto; no hubo cobro.

## F4: disputas

María inició con Q225.00. Se hicieron dos pasos nuevos:

- Paso 1: `paso_0472e520ddeba74e20b92dacf8498da294b4c629a990489d720e25cb01f11462`
- Cobro 1: `cobro_973c0d8601bfd4e6dd9016d17c9ac6bfddd18080a645c6067c23cba1b4c19c30`
- Paso 2: `paso_bed8aae2525af1d93dde1ec1fab0bbff32605bbd11fdbb104652ab672f721847`
- Cobro 2: `cobro_f180ae17fdae897981fb86ab521a68444c0e65b420e3615d5c768db71926c644`

Saldo después de ambos cobros: Q216.00.

| Disputa | Decisión | Estado | Devolución |
|---|---|---|---|
| `disputa_d00a2c8049c796e63bec553888c670a29fe90de409f34593bb804aacace1d3f9` | Manual: `RECHAZAR` | `RECHAZADA` | Ninguna |
| `disputa_6a1b1380b65f6debfd557338b4ef45c7588ed52eb0a718c3435f8f1007efe4b7` | Automática: `APROBAR` | `APROBADA` | `devolucion_56d0e5d5ccc2fbbcf748376cef324acc1cc2fc61d355c0f998ef078f65bff7e8` |

- Devolución automática: Q4.50.
- Saldo al cerrar F4: Q220.50.
- Resultado: una disputa rechazada y otra aprobada automáticamente.

Durante la preparación existieron dos disputas exploratorias anteriores que también vencieron automáticamente. Se conservaron sus devoluciones y no se ocultaron ni se restauró el saldo manualmente.

## F5: cobro APP y saldo insuficiente

La primera ejecución de Diego comenzó con Q5.00. El segundo paso se verificó después del plazo y terminó facturado. Se documentó esa secuencia.

Después se recargaron Q5.00 de forma explícita para repetir el ensayo con el estado pendiente visible.

Evidencia final:

- Paso cobrado: `paso_bce125060c17077050858c4b5a2207e25c820334ea643932cc73a0b876679d27`
- Paso pendiente: `paso_6609189491a5f975ba38cd862546090f1ca9ed26e58dc2f02e08135a84a8b3ea`
- Modalidad: `APP`
- Cobro: Q5.00
- Pendiente: Q5.00
- Saldo final al capturar la evidencia: Q0.00
- Movimientos: uno, únicamente para el paso cobrado.
- Vencimiento del pendiente: `2026-11-14T21:22:04Z`

El estado `PENDIENTE` se capturó antes del vencimiento. Posteriormente venció como parte normal del flujo.

## F6: reporte diario

Reporte generado para `2026-11-14`, intervalo local de Guatemala:

- Desde UTC: `2026-11-14T06:00:00Z`
- Hasta UTC exclusivo: `2026-11-15T06:00:00Z`

Para `PEAJE_ZONA10`:

| Concepto | Valor |
|---|---:|
| Cobros | Q137.00 |
| Devoluciones | Q13.50 |
| Neto | Q123.50 |
| Facturas por cobrar | Q178.50 |

Totales de todos los peajes:

| Concepto | Valor |
|---|---:|
| Cobros | Q147.00 |
| Devoluciones | Q13.50 |
| Neto | Q133.50 |
| Facturas por cobrar | Q178.50 |

El reporte fue visible en el portal de administración.

## F7: idempotencia de aviso repetido

María fue llevada explícitamente de Q220.50 a Q250.00 mediante una recarga documentada de Q29.50.

El mismo aviso TAG fue enviado tres veces con el mismo timestamp:

- `paso_id`: `paso_30ab72ab171a95633034388b711f701c5139987e4f2edbe342583eff6c087b34`
- Timestamp: `2026-11-14T21:30:21.530915Z`
- TAG: `TAG-001`
- Estado: `COBRADO`
- Monto: Q4.50
- Movimientos: uno
- Saldo final: Q245.50

Resultado: los tres reintentos conservaron el mismo `paso_id` y no produjeron cobros duplicados.

## Pruebas automatizadas

```bash
RUN_FLOWS_AWS=1 \
GP_STACK=guatepass-dev \
AWS_DEFAULT_REGION=us-east-1 \
python -m pytest tests/integration/test_flows.py -v
```

Resultado:

```text
10 passed
```

También se ejecutaron las pruebas AWS aisladas de concurrencia e idempotencia:

- Wallet: saldo Q0, un cobro Q5 y un pendiente.
- Recargas: dos recargas repetidas sin crédito adicional, cobro FIFO y dos pendientes.

## Matriz de cobertura

| Capacidad | Evidencia |
|---|---|
| Cobro TAG | F1 Juan |
| Cobro pendiente y recarga | F1 Andrés |
| Vencimiento y recargo | F2 |
| Registro externo con NIT | F3 Rosa |
| Registro externo sin contacto | F3 Carlos |
| Placa no identificada sin cobro | F3 P-000XXX |
| Disputa manual | F4, disputa rechazada |
| Disputa automática y devolución | F4, disputa aprobada |
| Cobro APP con saldo insuficiente | F5 |
| Reporte neto y facturas separadas | F6 |
| Idempotencia de evento repetido | F7 |
| Outbox y publicación asíncrona | Evidencia de Outbox por cada webhook |
| Consistencia de saldo y movimientos | F1, F4, F5 y F7 |
| Separación de roles y portales | Portal de conductor, operador y administración |
