# Tarea 11 — Registro externo aislado

## Arquitectura

La solución utiliza un stack externo independiente:

```text
guatepass-registro-dev
```

El stack contiene:

- API Gateway.
- Lambda del registro externo.
- Tabla DynamoDB propia.
- Secreto de Secrets Manager.
- Parámetro SSM de disponibilidad.

El stack principal `guatepass-dev` no lee directamente la tabla externa. Consulta exclusivamente el API mediante `src/integraciones/registro.py`.

## Despliegue externo

```bash
sam validate \
  --lint \
  --template-file externo/template.yaml \
  --region us-east-1

sam build \
  --template-file externo/template.yaml \
  --build-dir .aws-sam-registro

sam deploy \
  --template-file .aws-sam-registro/template.yaml \
  --stack-name guatepass-registro-dev \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset
```

## Siembra

La tabla externa se siembra con 12 filas:

```bash
.venv-task11/bin/python externo/seeds/seed.py \
  --stack guatepass-registro-dev \
  --csv seeds/guatepass/registro_transito.csv
```

El script es idempotente. Repetirlo no duplica registros.

## Contrato del API

```text
GET /registro/{placa}
```

Respuestas:

- `200`: placa encontrada.
- `404`: placa ausente.
- `401`: credenciales inválidas.
- `503`: servicio temporalmente no disponible.

Las credenciales se envían mediante Basic Authentication. El contenido del secreto nunca se copia al código.

## Apagado y encendido simulado

El apagado representa una indisponibilidad simulada del proveedor externo:

```bash
.venv-task11/bin/python scripts/registry_mode.py \
  --stack guatepass-registro-dev \
  --mode off
```

Para restaurar:

```bash
.venv-task11/bin/python scripts/registry_mode.py \
  --stack guatepass-registro-dev \
  --mode on
```

Mientras está apagado, el API devuelve `503`.

## Consumidor

El consumidor de `cola-registro`:

- Usa timeout para la llamada HTTPS.
- Convierte `404` en `NO_IDENTIFICADO`.
- Reintenta errores `5xx`, timeouts y errores de red.
- Trata credenciales inválidas como error operativo.
- Crea factura premium de Q7.50 usando propietario y NIT externos.
- Crea invitación solamente si existe email o teléfono.
- Es idempotente y no crea una segunda factura.

## Redrive de DLQ

La DLQ no se reprocesa automáticamente. Para redrive manual:

```bash
.venv-task11/bin/python scripts/redrive.py \
  --stack guatepass-dev \
  --queue registro
```

La retención de mensajes es finita, por lo que la DLQ debe monitorearse y recuperarse antes del vencimiento.

## Pruebas

Prueba de siembra repetible:

```bash
RUN_AWS_INTEGRATION=1 \
REGISTRY_STACK=guatepass-registro-dev \
REGISTRY_CSV=seeds/guatepass/registro_transito.csv \
.venv-task11/bin/python -m pytest \
  tests/integration/test_seed.py \
  -k registry_seed_is_repeatable \
  -v
```

Pruebas del API y de idempotencia:

```bash
RUN_AWS_INTEGRATION=1 \
REGISTRY_STACK=guatepass-registro-dev \
GP_STACK=guatepass-dev \
REGISTRY_IDEMPOTENCY_PASO_ID=<PASO_ID> \
.venv-task11/bin/python -m pytest \
  tests/integration/test_registry.py \
  -v
```

## Escenarios verificados

| Placa | Resultado |
|---|---|
| `P-888EFG` | Factura Q7.50 e invitación por email |
| `P-789GHI` | Factura Q7.50 sin invitación |
| `P-222MNO` | Factura Q7.50 e invitación por teléfono |
| `P-000XXX` | `NO_IDENTIFICADO`, sin factura |
| `P-123ABC` | Cobro local Q4.50 aunque el externo estuviera apagado |


