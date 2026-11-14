# Tarea 14: simulación, observabilidad e incidentes

Trabajar en `fabian/base` y partir del código de tarea 13. Estos comandos se ejecutan desde la raíz de GuatePass con `.venv312` activo. Los ensayos que manipulan colas se limitan a `Environment=dev`.

## Instalar y comprobar

```bash
python -m pytest tests/integration/test_load.py -v
python -m pytest tests/unit tests/integration -q
git diff --check
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
```

Desplegar con los parámetros existentes del stack (`sam deploy --stack-name guatepass-dev --region us-east-1 ...`) y comprobar `UPDATE_COMPLETE` antes de generar tráfico. La plantilla añade dashboard, métricas, alarmas y una Lambda de inspección del Outbox; desplegarla puede actualizar el nombre del REST API y sus métricas. No volver a publicar portales: esta tarea no cambia los archivos web.

## Cuatro modos y conciliación

El simulador obtiene la clave de cámara de API Gateway, la usa solo en memoria y crea `runs/<run_id>.json` (directorio ignorado por Git). Una respuesta HTTP 202 indica recepción; la conciliación comprueba el resultado durable. Usar una placa registrada con saldo suficiente para los cobros esperados y un peaje existente. El ensayo de 500 pasos puede agotar saldo y producir pasos PENDIENTE: esto no equivale a un doble cobro.

```bash
python scripts/simulate.py --stack guatepass-dev --mode single --plates P-123ABC --tag TAG-005
python scripts/reconcile.py --stack guatepass-dev --manifest "$(ls -t runs/*.json | head -1)"

python scripts/simulate.py --stack guatepass-dev --mode duplicate --plates P-123ABC --tag TAG-005
python scripts/reconcile.py --stack guatepass-dev --manifest "$(ls -t runs/*.json | head -1)"

python scripts/simulate.py --stack guatepass-dev --mode concurrent --count 2 --plates P-123ABC --tag TAG-005
python scripts/reconcile.py --stack guatepass-dev --manifest "$(ls -t runs/*.json | head -1)"

python scripts/simulate.py --stack guatepass-dev --mode load --count 500 --duration 60 --plates P-123ABC --tag TAG-005
LOAD_MANIFEST=$(ls -t runs/*.json | head -1)
python scripts/reconcile.py --stack guatepass-dev --manifest "$LOAD_MANIFEST" --wait-seconds 300
python scripts/observe.py --stack guatepass-dev --minutes 30
```

Registrar `latencia_respuestas.max_ms`, `p95_ms`, `p99_ms`, `over_1000ms`, `fallos_definitivos`, `pasos_faltantes`, `dobles_efectos` y `saldos_negativos`. Investigar toda respuesta >=1000 ms, aunque el promedio sea menor. `observe.py` distingue métricas sin datos (`data_points: 0`) de mediciones cero. Repetir `reconcile.py` tras la recuperación si hay pasos pendientes de procesar.

## Alarmas verificadas en CloudWatch

El dashboard `${STACK}-observability` reúne latencia y errores API, errores y throttles Lambda, edad/profundidad/DLQ de SQS, pendientes/edad y fallas de publicación Outbox y ejecuciones de espera fallidas. CloudWatch tarda algunos minutos en agregar los períodos de 60 segundos. El monitor de Outbox recorre las cuatro particiones de `PorPendiente`; nunca se invoca desde el webhook.

```bash
python scripts/drill_alarms.py --stack guatepass-dev --phase backlog
python scripts/drill_alarms.py --stack guatepass-dev --phase dlq
python scripts/observe.py --stack guatepass-dev --minutes 30
```

El ensayo de backlog comprueba cola vacía, pausa temporalmente el consumidor de cobros, introduce 12 copias de un evento registrado ya procesado, espera `ALARM`, reactiva el consumidor y espera `OK`. Los mensajes reutilizados son idempotentes. La segunda fase introduce un marcador exclusivo en la DLQ vacía y elimina solo ese marcador antes de esperar `OK`. Guardar la salida de ambas transiciones; una captura vacía del dashboard no sirve. Si la ejecución se interrumpe mientras el consumidor está pausado, obtener `PasosQueueArn` y `CobrarFunctionName` de los Outputs del stack, encontrar su event source mapping con `aws lambda list-event-source-mappings` y habilitarlo con `aws lambda update-event-source-mapping --uuid UUID --enabled --region us-east-1`.

## Incidente del registro externo

El registro simulado vive en el stack **`guatepass-registro-dev`**. Se necesita una placa registrada con saldo para comprobar cobros y otra que no exista ni tenga usuario/TAG en GuatePass para provocar consulta externa y factura. Antes de empezar, comprobar que la segunda placa no esté registrada en el sistema externo.

```bash
python scripts/registry_mode.py --stack guatepass-registro-dev --mode off
python scripts/simulate.py --stack guatepass-dev --mode concurrent --count 4 --plates P-123ABC,P-999ZZZ
INCIDENT_MANIFEST=$(ls -t runs/*.json | head -1)
python scripts/reconcile.py --stack guatepass-dev --manifest "$INCIDENT_MANIFEST" --wait-seconds 30
python scripts/registry_mode.py --stack guatepass-registro-dev --mode on
python scripts/redrive.py --stack guatepass-dev --queue registro
python scripts/reconcile.py --stack guatepass-dev --manifest "$INCIDENT_MANIFEST" --wait-seconds 300 --require-invoices
python scripts/observe.py --stack guatepass-dev --minutes 30
```

Con el proveedor apagado, el primer `reconcile.py` puede terminar con diferencias: guardar esa salida como evidencia del incidente. La restauración del proveedor debe ejecutarse incluso si falla algún paso intermedio; nunca dejar el modo `off` después del ensayo. `redrive.py` mueve solo mensajes que ya llegaron a la DLQ; si devuelve cero, esperar los reintentos de la cola principal y repetir la conciliación. Si la placa de ejemplo `P-999ZZZ` sí existe en el registro real, elegir una placa de prueba confirmada como inexistente. La conciliación final debe registrar cobros existentes, facturas `NO_REGISTRADO` de las placas no identificadas y cero efectos repetidos.

## Repetición de eventos con dinero

Reutilizar un cobro con disputa APROBADA de tarea 12, por ejemplo el par `cobro_id` / `disputa_id` que se verificó en aquella prueba:

```bash
python scripts/replay.py --stack guatepass-dev --cobro-id COBRO_ID --disputa-id DISPUTA_ID
```

El script vuelve a publicar `paso.registrado`, `paso.cobrado` y `disputa.resuelta` con los IDs originales; compara el saldo y los movimientos originales antes y después. El mismo `paso_id` debe conservar un solo cobro y el mismo `cobro_id` una sola devolución. El argumento `DISPUTA_ID` debe ser el de una disputa aprobada correspondiente al `COBRO_ID`.

## Interpretación de resultados

- El manifiesto almacena avisos, intentos, códigos, IDs y latencias, nunca API keys ni tokens. El contador de intentos incluye reintentos y el de pasos únicos agrupa avisos idénticos.
- `reconcile.py` recorre todas las páginas de las tablas involucradas con lectura consistente, compara Pasos, Movimientos, Facturas y saldos. Devuelve código 1 si encuentra diferencias.
- Los mensajes de Log de recepción, cobro, publicación y decisión de disputa usan campos permitidos `paso_id`, `evento_id`, `request_id`, `estado`, `error` sin contraseñas ni contacto. La fecha de operación se preserva en cada reintento.
- Aceptación en CloudShell: pruebas locales, despliegue y salidas reales de simulación, conciliación, alarmas y repetición. Este ZIP no contiene resultados fabricados de AWS.
