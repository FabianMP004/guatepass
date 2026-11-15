# Tarea 6 — instrucciones en CloudShell

Paquete sobre la base recibida. Mantiene WebhookFunction=1024 MB y también
UsersFunction=1024 MB tal como estaba en el ZIP del usuario.

1. Trabajar en fabian/base con tarea 5 guardada y coordinada con el equipo.
2. Extraer el ZIP en ~/tarea6-revision, revisar diff de template.yaml y copiar
   conservando rutas. Es una actualización, no un repositorio completo.
3. Revisar docs/eventos-v2.md, especialmente migración y contacto de invitación.

```bash
source .venv-task5/bin/activate
.venv-task5/bin/python -m pip install -r requirements-task6.txt
.venv-task5/bin/python -m pytest tests/unit tests/integration/test_outbox.py tests/integration/test_routing.py -q
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
sam deploy --template-file .aws-sam/build/template.yaml --stack-name guatepass-dev --region us-east-1 --capabilities CAPABILITY_IAM --resolve-s3
```

Conservar parámetros previos si usa --guided. Revisar UPDATE_COMPLETE.
El paquete cambia tests/integration/test_auth.py: con clave y cuerpo vacío
espera 400, no 501; el aviso válido 202 se comprueba con el script de webhook.

## Pruebas AWS

Ejecutar después de desplegar, en dev/demo. Las pruebas crean registros simulados
con placa TEST- única, no usuarios ni cobros. Conservan evidencia en DynamoDB.

```bash
RUN_AWS_INTEGRATION=1 GP_STACK=guatepass-dev AWS_REGION=us-east-1 \
  .venv-task5/bin/python -m pytest tests/integration/test_routing_aws.py -v -s
```

Dos pruebas deben pasar; la de DLQ queda omitida hasta habilitarla explícitamente.
La primera comprueba patrones REALES mediante TestEventPattern; la segunda
publica invitación en el bus y espera notificación persistida. Repite la entrega
y también invoca síncronamente el mismo handler para comprobar deduplicación
sin asumir que un tiempo arbitrario asegura procesamiento del segundo mensaje.

Prueba de mensaje malformado -> DLQ, con el consumidor real:

```bash
RUN_AWS_INTEGRATION=1 RUN_DLQ_TEST=1 GP_STACK=guatepass-dev AWS_REGION=us-east-1 \
  .venv-task5/bin/python -m pytest tests/integration/test_routing_aws.py::test_malformed_message_reaches_consumer_dlq -v -s
```

Tarda aproximadamente 15–20 minutos: visibility=180, maxReceiveCount=5.
No reduce visibility ni simula enviando directamente a DLQ. No purga ni elimina
mensajes; deja el mensaje de prueba como evidencia. Lee la DLQ y devuelve a
visibilidad inmediata los mensajes recibidos. Coordinar que nadie más esté
procesando la misma DLQ durante la prueba. Si expira, revisar logs de notificar,
event source mapping, métricas de cola y política redrive.

## Smoke test del webhook actualizado

El script de tarea 5 calculaba el ID antiguo PasoRegistrado. Ahora debe buscar
paso.registrado para pasos nuevos. Se incluye reemplazo compatible con v1/v2:

```bash
.venv-task5/bin/python scripts/probar_webhook_tarea5.py \
  --stack guatepass-dev --placa P-456DEF --peaje PEAJE_ZONA10
```

Genera un paso nuevo real de prueba; cola-pasos lo conservará hasta conectar el
consumidor de tarea 7. Podría cobrarlo entonces. No repetir innecesariamente.

## Límites y cierre

Tarea 6 implementa las tres colas, seis reglas (v2 y legacy), DLQ, Archive y
notificaciones simuladas. Consumidores de cobro, registro, esperas y tiempo real
se conectan en sus tareas; la tabla del catálogo indica qué destino sigue pendiente.
La entrega fallida de EventBridge tiene DLQ/política configurada, pero su prueba
de fallo inducido no se ejecuta por este paquete. Replay se documenta; no se
lanza automáticamente. No afirmar que se envió SMS/email.

Guardar en docs/historico/verificaciones/VERIFICACION-TAREA6.md resultados reales de pruebas, SAM, stack,
IDs de notificación, mensaje DLQ y revisión del catálogo. Abrir PR con revisor.
No subir .venv, .aws-sam, contraseñas, tokens ni cuerpos de invitación con contacto.

Fuentes técnicas:
- https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html
- https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html
- https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-dlq.html
- https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-archive.html
