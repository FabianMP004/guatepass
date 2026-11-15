# Tarea 7 — tarifas, tags y cobro atómico

Base: ZIP `guatepass-base-tarea7.zip` compartido después del PR de tarea 6.
El paquete contiene cambios, no reemplaza los CSV originales ni samconfig.toml.
Python 3.12, región us-east-1, stack guatepass-dev.

## 1. Instalar y comprobar localmente

El instalador del ZIP compara hashes antes de copiar. Si un archivo difiere tanto
de la base como del resultado esperado, se detiene sin sobrescribirlo.

```bash
cd ~/proyectos/guatepass
git status --short
source .venv-task5/bin/activate
.venv-task5/bin/python -m pip install -r requirements-task7.txt
.venv-task5/bin/python -m pytest tests/unit/test_tarifas.py tests/integration/test_wallet.py -v
.venv-task5/bin/python -m pytest -q
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
```

No avanzar si hay errores. Los tests reales de AWS se omiten por defecto.
No exportar RUN_AWS_INTEGRATION globalmente al correr toda la suite: se habilitaría
también la siembra y pruebas de tareas anteriores. Usar variables por comando.

## 2. Completar identidades de conductores sin resembrar

La base incluye integrantes.csv con administrador, atención y un operador. No
asigna carné a los siete conductores. El seed solo escribe carnet cuando existe
asignación. money.py exige sub y carnet de la identidad almacenada; NO toma el
carné de un aviso de cámara ni usa OwnerCarnet para un conductor conocido.

Decidir a quién se asignan las cuentas demo. Una opción explícita para trabajar
ahora como Fabián es asignar los conductores no asignados a Fabián, cuyo carné real
ya está en integrantes.csv. No se inventa ningún carné ni se crea usuario:

```bash
.venv-task5/bin/python scripts/completar_identidades_tarea7.py --stack guatepass-dev --integrante fabian
```

Revisar la lista. Para repartirlos entre los tres, agregar primero filas a
integrantes.csv con los usernames conductor_P-... y sus carnés reales; las filas
explícitas prevalecen sobre --integrante. Para aplicar la lista mostrada:

```bash
.venv-task5/bin/python scripts/completar_identidades_tarea7.py --stack guatepass-dev --integrante fabian --apply
```

Actualiza custom:carnet en Cognito y carnet/version en Usuarios. No cambia saldo,
contraseña, grupo, sub ni tags. Se puede reejecutar tras un fallo parcial. Si ya
hay otro carné, se detiene para revisar la asignación. Renueven sesiones abiertas.
Guardar integrantes.csv actualizado en el commit. No ejecutar seed_main para esto.

## 3. Primer despliegue: consumidor pausado

```bash
.venv-task5/bin/python scripts/desplegar_tarea7.py --consumer false --apply
aws cloudformation describe-stacks --stack-name guatepass-dev --region us-east-1 --query 'Stacks[0].StackStatus' --output text
```

Esperar UPDATE_COMPLETE. El script conserva los parámetros actuales, incluidos
timeouts demo 120 si siguen configurados. Usa .aws-sam/build/template.yaml.
No crea funciones a mano. El webhook no cambia y continúa escribiendo Outbox.

BillingConsumerEnabled=false solo pausa SQS → CobrarFunction; permite invocar la
función directamente para validar transacciones con fixtures aislados.

## 4. Evidencia real de dinero y permisos de tags

Primero probar dos pasos simultáneos de un usuario sintético con saldo Q5:

```bash
RUN_WALLET_AWS=1 GP_STACK=guatepass-dev AWS_DEFAULT_REGION=us-east-1 \
.venv-task5/bin/python -m pytest tests/integration/test_wallet_aws.py -v -s
```

Esta prueba usa las tablas existentes, crea un usuario/tarifa/pasos de prueba con
prefijo IT7-, invoca la Lambda desplegada en paralelo y comprueba un movimiento,
saldo Q0, un pendiente y que las repeticiones no cambian nada. No toca el saldo de
Diego real. Conserva fixtures identificados para evidencia; Outbox puede producir
notificaciones simuladas. No prueba por sí sola el transporte SQS.

Probar API real de tags con cuentas Cognito temporales:

```bash
RUN_AWS_INTEGRATION=1 GP_STACK=guatepass-dev AWS_DEFAULT_REGION=us-east-1 \
.venv-task5/bin/python -m pytest tests/integration/test_auth.py -k tag_routes -v
```

Comprueba 401/403, OPTIONS, asociación repetida, consulta, desactivación repetida
y saldo intacto. La fixture elimina sus usuarios temporales, conserva tag inactivo
y eventos históricos. No usa las contraseñas de las cuentas demo.

## 5. Revisar cola y activar consumo

Al habilitar el consumidor se procesarán avisos acumulados; pueden descontar saldo
de los clientes de las pruebas anteriores. Revisar conteos y pasos REGISTRADO.
No purgar colas, restablecer saldos ni eliminar eventos para simplificar la prueba.

```bash
.venv-task5/bin/python - <<'PY'
import boto3
s=boto3.Session(region_name='us-east-1')
o={x['OutputKey']:x['OutputValue'] for x in s.client('cloudformation').describe_stacks(StackName='guatepass-dev')['Stacks'][0]['Outputs']}
print(s.client('sqs').get_queue_attributes(QueueUrl=o['PasosQueueUrl'],AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible','MessageRetentionPeriod'])['Attributes'])
table=s.resource('dynamodb').Table(o['PasosTableName']);cursor=None
while True:
    args={'ConsistentRead':True,'FilterExpression':'#s=:s','ExpressionAttributeNames':{'#s':'estado'},'ExpressionAttributeValues':{':s':'REGISTRADO'}}
    if cursor:args['ExclusiveStartKey']=cursor
    page=table.scan(**args)
    for x in page.get('Items',[]):print({k:str(x.get(k)) for k in ['paso_id','placa','peaje_id','tag_id','estado','monto_centavos']})
    cursor=page.get('LastEvaluatedKey')
    if not cursor:break
PY
```

Los conteos SQS son aproximados. Pasos REGISTRADO no prueba por sí solo que todavía
exista mensaje en cola; la retención configurada es cuatro días. Si hubo caducidad,
republicar con ID original mediante el mecanismo de recuperación/replay adecuado.

Después de revisar y aceptar procesar los avisos pendientes:

```bash
.venv-task5/bin/python scripts/desplegar_tarea7.py --consumer true --apply
aws lambda list-event-source-mappings --function-name guatepass-dev-cobrar --region us-east-1 --query 'EventSourceMappings[].{Estado:State,Respuestas:FunctionResponseTypes}' --output table
```

Esperar UPDATE_COMPLETE y mapping Enabled. Hacer una prueba de webhook acordada
con una placa de demo (sí puede cobrarla) y comprobar Pasos/Movimientos/Outbox por
sus claves base. Las repeticiones deben conservar paso_id y un solo cobro_id.

Para pausar de nuevo se usa el mismo script --consumer false --apply. No modificar
el mapping por consola y dejarlo en drift. Conservar BillingConsumerEnabled=true
en futuros despliegues: el script lee el valor del stack, salvo el argumento dado.

## Comportamiento y límites

- Tarifas: TAG 90%, APP 100%, NO_REGISTRADO 150%; Decimal ROUND_HALF_UP al centavo.
- TAG exige existencia, activo=true y misma placa. Un tag de otro usuario no da descuento.
- Clasificación persistida con condición de versión + snapshots de usuario/tag/tarifa.
  Una caída después de clasificar no permite cambiar modalidad/monto al reintentar.
- Saldo, versión, estado, movimiento único y Outbox se confirman juntos. Un conflicto
  técnico provoca relectura/reintento acotado; si persiste falla el mensaje SQS.
- Sin fondos: condición saldo<monto, versión incrementada, Pendientes y vencimiento
  leído de SSM se confirman junto al evento. Una repetición no abre otra espera.
- La clave de Pendientes conserva el nombre físico orden=recibido_en#paso_id.
- Un paso ya PENDIENTE devuelve SIN_CAMBIO ante repetición del aviso, aunque después
  tenga saldo. El cobro ordenado de pendientes corresponde a la tarea de recargas.
- cola-pasos ya tiene reglas para saldo.recargado/saldo.devuelto desde tarea 6. Esta
  entrega NO los confirma como procesados: devuelve fallo parcial hasta implementar
  su flujo. No activar productores de recargas/devoluciones antes de conectarlo.
- POST/DELETE tag solo actualizan vínculo, version, tag y Outbox; no modifican dinero.
- Desactivar conserva la fila de Tags y eventos; libera el vínculo activo, guarda
  ultimo_tag_id y no cambia la tarifa de pasos ya clasificados.
- DELETE mantiene el tag objetivo durante sus reintentos internos. Una nueva petición
  DELETE posterior actúa sobre el tag que esté activo en ese momento.
- Toda escritura monetaria de las Lambdas pasa por common/money.py. Seeds y fixtures
  son inicialización explícita fuera del runtime; no constituyen recargas.
- No se implementan aún facturación, recargas, expiración, registro externo ni realtime.

## Evidencia y commit

Actualizar docs/historico/verificaciones/VERIFICACION-TAREA7.md con las salidas reales. No marcar los tests Moto,
AWS ni sam validate como aprobados sin ejecutarlos. La prueba local que conecta
republicación Outbox con cobro único está en test_wallet.py; AWS comprueba concurrencia
y duplicados de Lambda por separado. La caída real de publicadora fue comprobada
en tarea 5, pero no se simuló nuevamente en AWS con el cobro conectado.

```bash
git add template.yaml requirements-task7.txt src/common/dynamo.py src/common/money.py src/pasos/cobrar.py src/usuarios/tags.py tests/unit/test_tarifas.py tests/unit/test_infra_tarea7.py tests/integration/test_wallet.py tests/integration/test_wallet_aws.py tests/integration/test_auth.py scripts/completar_identidades_tarea7.py scripts/desplegar_tarea7.py docs/eventos-v2.md LEEME-TAREA7.md docs/historico/verificaciones/VERIFICACION-TAREA7.md
```

Si se aplicaron asignaciones, añadir seeds/guatepass/integrantes.csv. Revisar
git diff --cached --check y git diff --cached. Después commit, push a fabian/base
y revisión de Cynthia/Tono antes del PR a main. No incluir el archivo accidental.

Referencias: https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html
y https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html
