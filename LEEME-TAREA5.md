# Tarea 5 — incorporación en fabian/base

Este paquete modifica la plantilla de la tarea 3 y agrega módulos. No incluye
semillas, contraseñas ni samconfig.toml. No despliega recursos desde el chat.

## Contratos

- `registrar_paso(aviso)` devuelve `{paso_id}` tras confirmar la transacción.
- `publicar(eventos)` recibe sobres de docs/eventos.md, devuelve evento_id fallidos.
- `recuperar_outbox()` devuelve revisados, fallidos e incompleto.
- Se conserva `source=guatepass`, `detail-type=PasoRegistrado`. La frase
  paso.registrado en la tarea corresponde a este hecho, no a un segundo evento.
- Placas: una letra, guion opcional, tres dígitos y tres letras. Strip/upper;
  se conserva el guion (no cambiar IDs del contrato v1).
- Entrada de cámara UTC, terminación Z o +00:00, de cero a seis decimales.
  Más precisión se rechaza, no se trunca. Normalización a seis microsegundos.
- tag_id ausente/null se guarda null; tag no interviene en la identidad.
- recibido_en se toma del servidor. fecha_local usa la fecha del paso en Guatemala.
- Campos extras se rechazan, incluido carnet. La atribución usa OwnerCarnet.
- Un duplicado conserva estado y payload originales; siempre responde 202.

## Persistencia y entrega

ConditionCheck del peaje + Put de Pasos + Put de Outbox en una transacción.
IAM concede ConditionCheckItem, PutItem y GetItem a sus tablas concretas.
El publicador verifica existencia del bus, FailedEntryCount y cada entrada.
Solo se marca enviado después de aceptación. Si el marcado falla se vuelve a
publicar el mismo evento_id. No hay garantía exactly-once.
Stream filtra INSERT, reporta SequenceNumber fallido, tres reintentos, edad
máxima 3600 segundos y destino SQS existente. La recuperación recorre páginas
del índice cada cinco minutos y revalida el registro base consistentemente.
Un presupuesto de 40 segundos limita el recorrido; el próximo tick reintenta
los pendientes restantes. Vigilar backlog/errores: un evento inválido persistente
necesita intervención, no borrado automático.

Retención: NO TTL ni eliminación automática de Outbox durante el proyecto,
incluso para enviados. Así ningún pendiente expira antes de publicarse. Después
de la evaluación se archivará y definirá limpieza; no activarla sin decidir su
ventana de auditoría. Los logs conservan 14 días; DLQ según la plantilla existente.
Aceptación por EventBridge no demuestra procesamiento del consumidor; las reglas
y consumidores se conectan en tareas posteriores.

## Incorporación

Desde ~/proyectos/guatepass, en fabian/base, con trabajo previo guardado:

1. Extraer el ZIP en una carpeta temporal y comparar template.yaml antes de copiar.
2. Copiar los archivos conservando rutas. No reemplazar tests/integration/test_seed.py.
3. Agregar .venv-task5/ al .gitignore si aún falta.
4. Ejecutar:

```bash
.venv-task5/bin/python -m pip install -r requirements-task5.txt
.venv-task5/bin/python -m pytest tests/unit tests/integration/test_outbox.py -q
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
sam deploy --stack-name guatepass-dev --region us-east-1 --capabilities CAPABILITY_IAM
```

El último comando usa su samconfig.toml ya existente. Si necesita parámetros,
usar --guided y conservar OwnerCarnet y los valores anteriores.
No recrear las tablas ni volver a sembrar para esta tarea.

Las pruebas test_outbox.py son integración LOCAL con Moto y fallos inyectados,
no una comprobación de IAM, del Stream desplegado ni del bus real.
Aquí no se implementa ni se prueba un cobro: verificar un solo cobro eventual
queda pendiente de integrar al consumidor de cobros.

## Prueba en AWS y latencia externa

Tras UPDATE_COMPLETE, escoger una placa y peaje reales de los CSV:

```bash
.venv-task5/bin/python scripts/probar_webhook_tarea5.py \
  --stack guatepass-dev --placa P-456DEF --peaje ID_REAL_DEL_CSV
```

Este script crea UN paso nuevo y lo envía tres veces, comprueba ID estable y
marca de publicación en Outbox. Lee la API key en memoria y no la imprime.
El paso de prueba queda persistido: puede generar un cobro si ya hay consumidor.
No ejecutar mientras otro integrante modifica o prueba saldos de esa placa.
Conservar los tiempos HTTP y request_id; revisar cada respuesta >=1 segundo.
La primera llamada NO garantiza cold start: correlacionar REPORT con
`Init Duration` en /aws/lambda/guatepass-dev-webhook. Ejecutar una primera llamada
inmediatamente después de actualizar el código, y comprobar el log; si no hay
Init Duration, la muestra no acredita arranque frío. Registrar también llamadas
calientes. Para percentiles fiables se necesita una muestra mayor.

La antigua prueba de tarea 3 que espera 501 con API key debe actualizarse:
aviso válido -> 202; cuerpo inválido -> 400. test_auth.py no estaba en el ZIP y
no se modifica a ciegas. Conservar todas las demás comprobaciones de autorización.

## Recuperación real (ambiente dev coordinado)

1. En Lambda, función guatepass-dev-outboxpublisher, localizar el UUID del trigger
   DynamoDB y guardar si estaba Enabled.
2. Deshabilitar temporalmente ESE trigger, enviar un aviso válido y confirmar
   paso persistido y Outbox pendiente. El schedule sigue activo y puede publicarlo
   a los cinco minutos; hacer la inspección antes.
3. Esperar el schedule o invocar la publicadora con `{}`; comprobar enviado=true.
4. Restaurar el trigger a su estado original incluso si una comprobación falla.
5. Reenviar el aviso original: mismo paso_id, sin nuevo registro de negocio.

No poner concurrencia cero a la función compartida: también bloquearía la
recuperación. La prueba de publicación aceptada seguida de fallo de marcado se
inyecta localmente en test_outbox.py; no altera políticas del ambiente compartido.
Cuando exista el consumidor, añadir la aserción de un único movimiento COBRO
para ese paso ante dos entregas del mismo evento_id y reintentos concurrentes.

## Revisión

Revisar git diff, ejecutar las pruebas, guardar solo archivos de tarea 5 y abrir
PR fabian/base -> main. Adjuntar evidencia SAM, despliegue y prueba HTTP.
No afirmar cierre completo mientras falten pruebas AWS, latencia fría o cobro
idempotente integrado. La implementación de ingestión puede integrarse antes
dejando esas dependencias explícitas.
