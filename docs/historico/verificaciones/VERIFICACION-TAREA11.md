# Verificación de tarea 11

## Infraestructura

- Stack externo desplegado: `guatepass-registro-dev`.
- Stack principal desplegado: `guatepass-dev`.
- API Gateway, Lambda, DynamoDB, Secrets Manager y parámetro SSM creados.
- El consumidor principal no tiene permisos de lectura sobre la tabla externa.
- El secreto se genera mediante infraestructura y se entrega a la Lambda mediante ARN.

## Siembra

La siembra externa procesó 12 filas y fue repetible:

```text
rows_read: 12
rows_written: 12
new_records: 12
replayed_records: 0
```

La prueba `registry_seed_is_repeatable` pasó correctamente.

## API externo

Se verificaron las siguientes respuestas:

| Caso | Resultado |
|---|---|
| Placa registrada con credenciales válidas | HTTP 200 |
| Placa inexistente | HTTP 404 `PLACA_NO_ENCONTRADA` |
| Credenciales inválidas | HTTP 401 `CREDENCIALES_INVALIDAS` |
| Servicio apagado | HTTP 503 `REGISTRO_NO_DISPONIBLE` |
| Servicio restaurado | HTTP 200 |

El apagado representa una indisponibilidad simulada del servicio externo.

## Escenarios funcionales

### P-888EFG

- Estado: `FACTURADO`.
- Monto: `750` centavos.
- NIT: `2234567-1`.
- Invitación: generada por email.

### P-789GHI

- Estado: `FACTURADO`.
- Monto: `750` centavos.
- NIT: `3456789-3`.
- Sin invitación porque no tiene email ni teléfono.

### P-222MNO

- Estado: `FACTURADO`.
- Monto: `750` centavos.
- NIT: `5678901-5`.
- Invitación generada únicamente con teléfono `50267890123`.

### P-000XXX

- Estado: `NO_IDENTIFICADO`.
- Sin factura.
- Sin invitación.

## Recuperación del servicio

Se creó un paso nuevo para `P-888EFG` con el registro externo apagado.

Antes de restaurar:

```text
estado: NO_REGISTRADO
ApproximateNumberOfMessagesNotVisible: 1
```

Después de ejecutar el comando de encendido y esperar la visibilidad de la cola:

```text
estado: FACTURADO
version: 3
```

Esto confirma que la cola retomó el procesamiento después de restaurar el servicio.

## Independencia del cobro local

Con el registro externo apagado, `P-123ABC` fue procesada por el flujo local:

```text
estado: COBRADO
monto_centavos: 450
modalidad: TAG
```

El cobro local continuó sin depender de la disponibilidad del registro externo.

## Redrive de DLQ

Se ejecutó:

```bash
.venv-task11/bin/python scripts/redrive.py \
  --stack guatepass-dev \
  --queue registro
```

Resultado:

```text
Status: COMPLETED
ApproximateNumberOfMessagesMoved: 1
Mensajes restantes en DLQ: 0
```

El redrive es manual; la DLQ no se reprocesa automáticamente.

## Idempotencia

La prueba de integración verificó que reprocesar el mismo evento conserva:

- El mismo `factura_id`.
- La misma versión del paso.
- Una sola factura asociada.

Resultado de las pruebas:

```text
tests/integration/test_registry.py
2 passed
```


