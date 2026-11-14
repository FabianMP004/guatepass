# Guion de reconstrucción y demostración

Este documento acompaña la presentación de la tarea 16. La demo usa dev, mantiene separados el stack principal y el registro externo, y no muestra contraseñas, claves API ni el valor del secreto de Secrets Manager.

## 1. Antes de iniciar

Registrar:

- commit exacto que se va a reconstruir;
- hora de inicio;
- identidad AWS y región;
- estado de existencia de guatepass-dev y guatepass-registro-dev;
- tiempo de cada despliegue y de CloudFront.

Comprobar que el stack principal no exista antes de la reconstrucción:

~~~bash
aws cloudformation describe-stacks \
  --stack-name guatepass-dev \
  --region us-east-1 \
  --query "Stacks[0].StackStatus" \
  --output text
~~~

El error ValidationError por stack inexistente es el resultado esperado para una reconstrucción desde cero. Si existe, mostrarlo y ejecutar la limpieza autorizada antes de comenzar.

## 2. Orden de reconstrucción

1. Activar el entorno virtual e instalar requirements-task10.txt.
2. Desplegar externo/template.yaml como guatepass-registro-dev.
3. Leer RegistroApiUrl y RegistroSecretArn desde Outputs.
4. Desplegar template.yaml pasando esos dos valores al stack principal.
5. Sembrar registro_transito.csv.
6. Sembrar clientes.csv, peajes.csv, integrantes.csv y Cognito con GP_DEMO_PASSWORD introducida de forma segura.
7. Activar los consumidores con el segundo sam deploy.
8. Ejecutar scripts/export_config.py y scripts/publish_portals.py.
9. Confirmar los estados CREATE_COMPLETE o UPDATE_COMPLETE.
10. Ejecutar las pruebas unitarias, de integración y de flujos.

La reconstrucción debe hacerse sólo con el README y el checkout; no se corrigen nombres ni parámetros manualmente desde la consola.

## 3. Preparación de ventanas

Abrir ventanas separadas para:

- conductor;
- operador de peaje Zona 10;
- atención;
- administrador;
- terminal de CloudShell para avisos de cámara y evidencia AWS.

Usar el portal publicado por CloudFront. La respuesta del webhook sólo confirma recepción durable; la evidencia definitiva se toma de DynamoDB, Outbox, logs y portales.

## 4. Orden de los siete flujos

### F1: TAG y recarga

1. Juan (P-123ABC) asocia TAG-005, inicia con Q100 en la siembra aislada y recibe un paso en PEAJE_ZONA10.
2. Mostrar el mismo paso_id, el movimiento COBRO por Q4.50, saldo Q95.50, historial y operador.
3. Andrés (P-666YZA) inicia con Q0, recibe un paso con TAG-003, queda PENDIENTE por Q4.50 y muestra el pendiente.
4. Recargar Q20 con una clave de idempotencia nueva; mostrar el cobro posterior y saldo Q15.50.

### F2: factura por vencimiento

1. En un ambiente aislado, repetir el caso de Andrés sin recargar.
2. Confirmar el plazo demo de 120 segundos.
3. Antes del vencimiento, mostrar el paso PENDIENTE.
4. Después del vencimiento, mostrar factura Q29.50, saldo Q0 y ningún movimiento de cobro.

### F3: registro externo

1. Rosa (P-888EFG) no tiene usuario de conductor: enviar el paso y mostrar desde backend, logs y operador la factura Q7.50, NIT y la invitación.
2. Carlos (P-789GHI) debe producir factura Q7.50 con NIT, pero sin invitación porque el registro no tiene contacto.
3. P-000XXX debe terminar NO_IDENTIFICADO, sin factura y sin movimiento.

Para la demostración del registro se puede usar:

~~~bash
python scripts/registry_mode.py \
  --stack guatepass-registro-dev \
  --region us-east-1 \
  --mode off

python scripts/registry_mode.py \
  --stack guatepass-registro-dev \
  --region us-east-1 \
  --mode on
~~~

### F4: disputas

1. María (P-456DEF, TAG-001) inicia con Q250.
2. Enviar dos pasos de Zona 10 y guardar ambos paso_id y cobro_id.
3. Abrir una disputa por cada cobro desde el portal.
4. Atención rechaza una manualmente.
5. La segunda se deja vencer para mostrar aprobación automática y devolución.
6. Confirmar saldo Q241 después de los dos cobros; al aprobar una disputa, registrar la devolución de Q4.50 y el saldo Q245.50, siempre que no existan otros pendientes de preparación.

La devolución es un movimiento independiente; el cobro original no se borra.

### F5: saldo insuficiente APP

1. Diego (P-444STU) inicia con Q5.
2. Enviar dos pasos APP de Zona 10 con timestamps distintos.
3. Capturar antes del vencimiento: un paso COBRADO por Q5, otro PENDIENTE por Q5 y saldo Q0.
4. Mostrar que existe un solo movimiento de cobro.

### F6: reporte

1. Generar el reporte del día con RevenueFunctionName.
2. Mostrar cobros, devoluciones y facturas por cobrar por separado.
3. Mostrar el total de PEAJE_ZONA10 y la consulta desde administración.

### F7: idempotencia

1. María inicia con Q250 y conserva TAG-001.
2. Enviar tres veces el mismo aviso, con el mismo timestamp.
3. Mostrar el mismo paso_id, un movimiento, un solo cobro por Q4.50 y saldo Q245.50.

## 5. Verificación automatizada

~~~bash
python -m pytest tests/unit -v
python -m pytest tests/integration -v

RUN_FLOWS_AWS=1 \
GP_STACK=guatepass-dev \
AWS_DEFAULT_REGION=us-east-1 \
python -m pytest tests/integration/test_flows.py -v
~~~

No se deben considerar aprobadas las pruebas marcadas SKIPPED por falta de una variable RUN_*. Las pruebas que tardan por diseño, como redrive de DLQ, se anotan con su tiempo y evidencia.

## 6. Evidencia mínima por flujo

Para cada flujo guardar:

| Elemento | Evidencia |
|---|---|
| estado inicial | saldo, TAG, usuario, plazo y stack |
| acción | comando o pantalla utilizada |
| identidad | paso_id, cobro_id, factura_id o disputa_id |
| resultado | estado, monto, saldo y cantidad de movimientos |
| AWS | tabla, Outbox, CloudWatch, SQS o Step Functions |
| portal | pantalla de conductor, operador, atención o administración |

No restaurar saldos a escondidas. Si se prepara un saldo para una prueba posterior, registrar la recarga o los pasos que produjeron el valor.

## 7. Limpieza después de calificar

Mostrar primero el plan:

~~~bash
python scripts/destroy.py \
  --stack guatepass-dev \
  --external-stack guatepass-registro-dev \
  --region us-east-1
~~~

Después de la autorización:

~~~bash
time python scripts/destroy.py \
  --stack guatepass-dev \
  --external-stack guatepass-registro-dev \
  --region us-east-1 \
  --execute \
  --confirm "DELETE guatepass-dev guatepass-registro-dev"
~~~

Conservar la salida con remaining_stacks, remaining_resources, secretos programados y elapsed_seconds. El secreto externo puede permanecer en recuperación programada; la reconstrucción siguiente debe aceptar el ARN nuevo generado por el stack externo.
