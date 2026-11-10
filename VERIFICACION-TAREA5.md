# Evidencia de preparación

- 25 pruebas existentes ejecutadas mediante unittest: OK.
- Sintaxis de todos los módulos nuevos comprobada con compileall: OK.
- YAML parseado y referencias de entorno comprobadas: OK.
- Pruebas nuevas pytest/Moto: NO EJECUTADAS aquí por falta de dependencias
  y acceso al índice de paquetes. Deben ejecutarse en CloudShell.
- sam validate --lint, sam build, despliegue y pruebas AWS: PENDIENTES.
- Cobro único eventual: pendiente del consumidor de cobros.

Documentación consultada:
- https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-putevents.html
- https://docs.aws.amazon.com/eventbridge/latest/APIReference/API_PutEvents.html
- https://docs.aws.amazon.com/lambda/latest/dg/services-ddb-batchfailurereporting.html

La comprobación del bus evita el caso documentado de PutEvents hacia un bus
inexistente con HTTP 200. Aun así, aceptación no garantiza efecto del consumidor.
# Verificación — Tarea 5

## Comprobaciones aprobadas

- 50 pruebas locales aprobadas:
  `python -m pytest tests/unit tests/integration/test_outbox.py -q`
- Validación SAM con lint: correcta.
- Construcción SAM: Build Succeeded.
- Stack guatepass-dev: UPDATE_COMPLETE.
- Webhook desplegado con 1024 MB de memoria.
- Tres envíos del mismo aviso devolvieron el mismo paso_id.
- Publicación confirmada mediante enviado=true en Outbox.

## Latencia observada

Con 256 MB, la primera respuesta HTTP tardó 1.6486 segundos.

Después del ajuste a 1024 MB:
- Respuesta HTTP con arranque frío: 0.8426 segundos.
- Init Duration: 323.75 ms.
- Duración del handler: 335.26 ms.
- Repeticiones del aviso: 0.0527 y 0.0381 segundos.

El arranque frío fue confirmado mediante los logs de Lambda.
Esta muestra cumple el objetivo de menos de un segundo;
no constituye una garantía bajo carga.

## Recuperación comprobada en AWS

Se pausaron temporalmente el trigger del Stream y la regla
programada de recuperación.

Resultados:
1. El webhook persistió el paso y dejó el evento pendiente.
2. La recuperación invocada manualmente publicó el evento.
3. Reenviar el aviso conservó el paso_id y recibido_en originales.
4. La regla programada y el trigger quedaron habilitados nuevamente.

Identificadores de evidencia:
- paso_id: paso_a88cf9404bd22f481f92eac5f29813cd5e260915c2efd8688292fc6a6d3e1023
- evento_id: evento_ccd6db303afcb1020be673cd77968fec61d7d45dbd07c1b9f968d849e0578083

## Fallos comprobados localmente

Las pruebas con Moto y fallos inyectados verificaron:
- Fallos parciales de PutEvents.
- Recuperación de eventos pendientes.
- Publicación aceptada seguida de fallo al marcar enviado.
- Republicación conservando el mismo evento_id.
- Recuperación paginada.

Estas pruebas locales no sustituyen pruebas de fallos equivalentes
en AWS. La entrega es al menos una vez y puede producir duplicados.

## Pendientes de integración

- Conectar y probar el consumidor de cobros.
- Demostrar un único movimiento COBRO por paso ante entregas
  duplicadas y reintentos concurrentes.
- Actualizar y ejecutar la prueba de autorización de tarea 3 que
  esperaba HTTP 501 del webhook: ahora un aviso válido devuelve 202.
- Completar mediciones de latencia bajo carga.

## Documentación consultada

- https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-putevents.html
- https://docs.aws.amazon.com/eventbridge/latest/APIReference/API_PutEvents.html
- https://docs.aws.amazon.com/lambda/latest/dg/services-ddb-batchfailurereporting.html
