# GuatePass

Proyecto final de Cloud & Inhouse Infrastructure. La solución registra pasos de vehículos, cobra por TAG o aplicación, administra saldos y recargas, emite facturas, procesa disputas y publica un reporte diario. El registro de tránsito externo se despliega en un stack separado.

## 1. Alcance y separación de ambientes

La entrega contiene dos stacks independientes:

| Componente | Stack de demo | Propósito |
|---|---|---|
| GuatePass principal | `guatepass-dev` | API, portales, cobros, facturas, disputas, eventos y reporte |
| Registro externo | `guatepass-registro-dev` | API privado simulado, tabla de tránsito, secreto y disponibilidad |

`dev`, `demo` y `prod` deben usar stacks, parámetros, tablas, colas, logs y buckets separados. Los comandos de este README usan `dev` para la demostración. No se deben reutilizar recursos de producción en el ensayo ni guardar secretos en Git.

El stack principal nunca lee directamente la tabla del registro externo: usa el API HTTPS y recibe desde CloudFormation únicamente la URL y el ARN del secreto. El secreto es generado por CloudFormation y su valor no forma parte del código, del ZIP ni de `portales/config.json`.

## 2. Requisitos

- Cuenta AWS con permisos para CloudFormation/SAM, Lambda, API Gateway, DynamoDB, Cognito, SQS, EventBridge, Step Functions, AppSync, S3, CloudFront, Secrets Manager, SSM, Scheduler, CloudWatch y Logs.
- AWS CLI configurado y `aws sts get-caller-identity` funcionando.
- AWS SAM CLI y Git.
- Python 3.12 o posterior. La ejecución validada del proyecto usó Python 3.13.
- Un checkout limpio de la rama de trabajo.
- Un usuario de GitHub con permiso de escritura si se necesita publicar la rama.

Comprobación inicial:

```bash
git status --short --branch
aws sts get-caller-identity
aws configure get region
sam --version
python3 --version
```

Preparación local recomendada:

```bash
cd /home/cloudshell-user/proyectos/guatepass
python3 -m venv .venv-task16
source .venv-task16/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-task10.txt
export AWS_DEFAULT_REGION=us-east-1
export AWS_REGION=us-east-1
```

En una reconstrucción desde checkout limpio, no se debe usar un entorno virtual incluido en el ZIP. El archivo `src/requirements.txt` corresponde al runtime administrado de Lambda; para las pruebas locales se usa `requirements-task10.txt`.

## 3. Configuración sin secretos

Los parámetros no sensibles se mantienen en `samconfig.toml` y en los argumentos de despliegue:

```bash
export AWS_DEFAULT_REGION=us-east-1
export ENVIRONMENT=dev
export MAIN_STACK=guatepass-dev
export EXTERNAL_STACK=guatepass-registro-dev
export OWNER_CARNET=20230223
export RECARGA_TIMEOUT=120
export DISPUTA_TIMEOUT=120
```

No se deben poner en archivos versionados:

- contraseñas de Cognito;
- el valor de `GP_DEMO_PASSWORD`;
- la clave del webhook;
- el contenido del secreto de Secrets Manager;
- tokens de GitHub o credenciales AWS;
- `portales/config.json`, que se genera después del despliegue.

Para otros ambientes se conservan los mismos parámetros en una configuración separada, cambiando como mínimo `Environment`, nombres de stack, región si aplica, `OwnerCarnet` y plazos. Los ARNs, URLs y nombres físicos se obtienen de Outputs; no se escriben manualmente en el código.

## 4. Despliegue reproducible

El registro externo siempre se despliega primero. Así se obtiene una URL y un ARN de secreto nuevos antes de crear o actualizar el stack principal.

### 4.1 Registro externo

```bash
sam validate \
  --lint \
  --template-file externo/template.yaml \
  --region "$AWS_DEFAULT_REGION"

sam build \
  --template-file externo/template.yaml \
  --build-dir .aws-sam-registro

sam deploy \
  --template-file .aws-sam-registro/template.yaml \
  --stack-name "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --parameter-overrides Environment="$ENVIRONMENT"
```

Leer los Outputs sin revelar el valor del secreto:

```bash
REGISTRY_API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='RegistroApiUrl'].OutputValue | [0]" \
  --output text)

REGISTRY_SECRET_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='RegistroSecretArn'].OutputValue | [0]" \
  --output text)

printf 'RegistroApiUrl=%s\nRegistroSecretArn=%s\n' \
  "$REGISTRY_API_URL" "$REGISTRY_SECRET_ARN"
```

Si una versión del template expone los alias `RegistryApiUrl` y `RegistrySecretArn`, se pueden consultar esos nombres. No ejecutar `aws secretsmanager get-secret-value` durante la presentación.

### 4.2 Stack principal

Se despliega inicialmente con los consumidores deshabilitados. Esto permite sembrar datos y revisar la identidad antes de comenzar el procesamiento asíncrono.

```bash
sam validate \
  --lint \
  --template-file template.yaml \
  --region "$AWS_DEFAULT_REGION"

sam build \
  --template-file template.yaml \
  --build-dir .aws-sam

deploy_main() {
  sam deploy \
    --template-file .aws-sam/template.yaml \
    --stack-name "$MAIN_STACK" \
    --region "$AWS_DEFAULT_REGION" \
    --capabilities CAPABILITY_IAM \
    --resolve-s3 \
    --s3-prefix "$MAIN_STACK" \
    --no-confirm-changeset \
    --parameter-overrides \
      Environment="$ENVIRONMENT" \
      OwnerCarnet="$OWNER_CARNET" \
      RecargaTimeoutSeconds="$RECARGA_TIMEOUT" \
      DisputaTimeoutSeconds="$DISPUTA_TIMEOUT" \
      BillingConsumerEnabled="${BILLING_CONSUMER_ENABLED:-false}" \
      RegistryApiUrl="$REGISTRY_API_URL" \
      RegistrySecretArn="$REGISTRY_SECRET_ARN" \
      RegistryConsumerEnabled="${REGISTRY_CONSUMER_ENABLED:-false}"
}

BILLING_CONSUMER_ENABLED=false
REGISTRY_CONSUMER_ENABLED=false
deploy_main
```

### 4.3 Siembra de ambos stacks

La siembra externa usa `registro_transito.csv` y es idempotente:

```bash
python externo/seeds/seed.py \
  --stack "$EXTERNAL_STACK" \
  --csv seeds/guatepass/registro_transito.csv
```

La siembra principal usa los CSV de `seeds/guatepass`. La contraseña se solicita de forma interactiva y vive sólo en la variable temporal del shell:

```bash
read -r -s -p "Contraseña demo de Cognito: " GP_DEMO_PASSWORD
echo
export GP_DEMO_PASSWORD

python seeds/seed.py \
  --stack "$MAIN_STACK" \
  --integrantes seeds/guatepass/integrantes.csv

unset GP_DEMO_PASSWORD
```

La siembra normal no reinicia saldos, versiones, activación de TAG ni contraseñas de Cognito. Para un ensayo aislado se deben usar datos de prueba y documentar cada saldo inicial; nunca se deben editar los CSV originales para simular un resultado.

Después de revisar la siembra, se activan los consumidores mediante el mismo despliegue, conservando la URL y el ARN obtenidos de Outputs:

```bash
BILLING_CONSUMER_ENABLED=true
REGISTRY_CONSUMER_ENABLED=true
deploy_main
```

Verificar el estado:

```bash
aws cloudformation describe-stacks \
  --stack-name "$MAIN_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].StackStatus" \
  --output text

aws cloudformation describe-stacks \
  --stack-name "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].StackStatus" \
  --output text
```

Ambos deben terminar en `CREATE_COMPLETE` o `UPDATE_COMPLETE`.

## 5. Publicación de portales

La configuración pública se deriva de Outputs y no incluye la clave del webhook:

```bash
python scripts/export_config.py \
  --stack "$MAIN_STACK" \
  --region "$AWS_DEFAULT_REGION"

python scripts/publish_portals.py \
  --stack "$MAIN_STACK" \
  --region "$AWS_DEFAULT_REGION"
```

El segundo comando publica `index.html`, `app.js`, `live.mjs`, `styles.css` y `config.json`, y crea una invalidación de CloudFront. Esperar a que la invalidación termine antes de abrir el portal. `portales/config.json` es generado, no se versiona ni se incluye en el ZIP.

## 6. Pruebas

Pruebas locales:

```bash
python -m pytest tests/unit -v
python -m pytest tests/integration -v
```

Las pruebas AWS son opt-in para evitar mutaciones accidentales:

```bash
RUN_FLOWS_AWS=1 \
GP_STACK="$MAIN_STACK" \
AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
python -m pytest tests/integration/test_flows.py -v
```

Pruebas de siembra repetible:

```bash
RUN_AWS_INTEGRATION=1 \
GP_STACK="$MAIN_STACK" \
REGISTRY_STACK="$EXTERNAL_STACK" \
AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
python -m pytest tests/integration/test_seed.py -v
```

La suite de flujos de la tarea 15 deja evidencia en `docs/evidencias.md` y cubre TAG, recarga, vencimiento, registro externo, disputas, saldo insuficiente, reporte e idempotencia. Las placas externas sin usuario se verifican desde DynamoDB, logs y portal de operador.

## 7. Plazos de demostración

Los plazos del stack normal son 86400 segundos. Para la demo se usan 120 segundos:

```bash
aws ssm get-parameter \
  --name "/guatepass/${ENVIRONMENT}/recarga/timeout-seconds" \
  --region "$AWS_DEFAULT_REGION" \
  --query Parameter.Value \
  --output text

aws ssm get-parameter \
  --name "/guatepass/${ENVIRONMENT}/disputa/timeout-seconds" \
  --region "$AWS_DEFAULT_REGION" \
  --query Parameter.Value \
  --output text
```

El cambio persistente se hace con `sam deploy`, pasando `RecargaTimeoutSeconds` y `DisputaTimeoutSeconds`. No se debe alterar el parámetro en consola sin registrar el cambio. Para restaurar operación normal se vuelve a desplegar con `86400` o el valor aprobado.

## 8. Disponibilidad del registro externo y redrive

La indisponibilidad simulada se controla mediante el Output del parámetro, no con un nombre hardcodeado:

```bash
python scripts/registry_mode.py \
  --stack "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --mode off

python scripts/registry_mode.py \
  --stack "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --mode on
```

El redrive es manual y se ejecuta únicamente después de revisar la causa y la DLQ:

```bash
python scripts/redrive.py \
  --stack "$MAIN_STACK" \
  --queue registro \
  --region "$AWS_DEFAULT_REGION" \
  --rate 10
```

La retención de una DLQ es finita. Se deben conservar el número de mensajes, la hora, el `task_handle` y la evidencia posterior del consumidor.

## 9. Reporte diario

El reporte usa la fecha local de Guatemala, separa cobros, devoluciones y facturas por cobrar, y persiste una versión idempotente en la tabla de recaudación:

```bash
FECHA=$(TZ=America/Guatemala date +%F)
REVENUE_FUNCTION=$(aws cloudformation describe-stacks \
  --stack-name "$MAIN_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='RevenueFunctionName'].OutputValue | [0]" \
  --output text)

aws lambda invoke \
  --function-name "$REVENUE_FUNCTION" \
  --region "$AWS_DEFAULT_REGION" \
  --invocation-type RequestResponse \
  --cli-binary-format raw-in-base64-out \
  --payload "{\"fecha\":\"$FECHA\"}" \
  /tmp/reporte-f6.json

python -m json.tool /tmp/reporte-f6.json
```

Para la consulta administrativa se usa `GET /revenue/{fecha}` con el usuario administrador. El reporte no sustituye la conciliación financiera de producción.

## 10. Seguridad y operación

- Los conductores, atención, operadores y administradores usan Cognito y grupos con autorización ligada a su placa, carnet o peaje.
- El webhook de cámara usa API key sólo para la demo y nunca se publica en el portal.
- El registro externo usa Basic Authentication con la credencial guardada en Secrets Manager; Lambda recibe el ARN y solicita el secreto en runtime.
- DynamoDB usa cifrado y recuperación point-in-time donde está definido por el template.
- Los eventos se publican mediante Outbox, EventBridge y SQS; los consumidores son idempotentes y tienen DLQ.
- Los cobros y devoluciones se registran como movimientos contables separados. Una devolución no borra el cobro original.
- Los reintentos del mismo aviso conservan `paso_id` y no producen un segundo movimiento.

La API key cumple el contrato de la demo, pero AWS no la recomienda como autenticación suficiente para cámaras en producción. La evolución prevista es mTLS o firma SigV4 con credenciales rotables, identidad por cámara, rate limiting, WAF y una red privada cuando el escenario lo permita.

Límites y mejoras de producción pendientes:

- límites por cámara, peaje, placa y tamaño de payload;
- autenticación fuerte y rotación automática de credenciales;
- retención y costo de CloudWatch Logs medidos por volumen real;
- recuperación prolongada de Step Functions y DLQ con runbooks y alarmas;
- conciliación financiera por período, peaje y movimiento, con revisión humana de diferencias;
- minimización, cifrado, retención y control de acceso para NIT, teléfono, correo, placa y movimientos;
- separación estricta de cuentas AWS y presupuestos entre demo y producción.

## 11. Limpieza y verificación de ausencia

Primero mostrar el plan; no modifica AWS:

```bash
python scripts/destroy.py \
  --stack "$MAIN_STACK" \
  --external-stack "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION"
```

La ejecución detiene reglas y schedules, deshabilita mappings de SQS, detiene esperas activas, desactiva CloudFront, vacía buckets incluyendo versiones y delete markers, elimina primero el stack principal y después el externo, y verifica Outputs y recursos retenidos.

Sólo después de confirmar que se puede borrar el ambiente:

```bash
time python scripts/destroy.py \
  --stack "$MAIN_STACK" \
  --external-stack "$EXTERNAL_STACK" \
  --region "$AWS_DEFAULT_REGION" \
  --execute \
  --confirm "DELETE $MAIN_STACK $EXTERNAL_STACK"
```

La salida debe mostrar:

- `remaining_stacks: []`;
- `remaining_resources: []`;
- el tiempo total (`elapsed_seconds`);
- secretos eventualmente programados para borrado.

CloudFront puede tardar varios minutos mientras se deshabilita y se elimina; se debe cronometrar antes de la presentación. El secreto externo puede quedar en ventana de recuperación de siete días según la política de Secrets Manager. Eso no significa que el código dependa de él: al reconstruir se crea un stack externo nuevo y se pasan sus nuevos Outputs al principal. No se debe cancelar la eliminación para reutilizar un ARN viejo.

La verificación cubre tablas DynamoDB, user pool y cliente de Cognito, colas y DLQ, EventBridge bus/archive/rules, parámetros SSM, logs, bucket y distribución CloudFront, APIs, Lambdas, mappings, schedules y máquinas Step Functions.

## 12. Empaquetado de la entrega

El empaquetador usa sólo archivos versionados y falla si el checkout está sucio. Primero se deben incluir y probar todos los cambios:

```bash
git status --short --branch
git diff --check
git add README.md docs/demo.md docs/arquitectura.pdf scripts/destroy.py scripts/package.py
git commit -m "Completa reconstrucción y entrega de tarea 16"
git status --short --branch
```

Crear el ZIP desde ese commit:

```bash
python scripts/package.py --output guatepass.zip
```

El empaquetador exige README, templates, state machine, scripts de siembra y los cuatro CSV. Excluye `.git`, entornos virtuales, `.aws-sam*`, `node_modules`, `__pycache__`, reportes temporales, `.env*`, credenciales, llaves, `guatepass.zip` y `portales/config.json`.

Verificar contenido y ausencia de archivos sensibles:

```bash
unzip -l guatepass.zip | sed -n '1,240p'
unzip -l guatepass.zip | grep -E 'clientes.csv|integrantes.csv|peajes.csv|registro_transito.csv|seeds/seed.py|externo/seeds/seed.py'
unzip -l guatepass.zip | grep -Ei '(^|/)(\.git|\.venv|\.aws-sam|node_modules|credentials|\.env|config\.json)' && echo 'REVISAR' || true
sha256sum guatepass.zip
```

El PDF final se entrega por separado como `docs/arquitectura.pdf`. Después de generar el ZIP autorizado no se debe cambiar código ni documentación sin volver a ejecutar pruebas, commit, empaquetado y verificación.

## 13. Reconstrucción independiente para la presentación

Otra persona debe comenzar con un checkout limpio y seguir este README sin modificar recursos manualmente en la consola. Registrar:

1. hora de inicio y commit probado;
2. duración de instalación y validación;
3. duración del despliegue externo;
4. duración del despliegue principal;
5. duración de ambas siembras y publicación de portales;
6. cualquier comando que haya requerido aclaración;
7. hora de limpieza y tiempo de CloudFront.

Antes de presentar se muestra que el stack principal no existe, se lanza la reconstrucción entre 20 y 30 minutos antes y se demuestra que el stack externo también es reproducible. Después de la calificación se ejecuta la limpieza de ambos sistemas y se conserva la salida del verificador.

## 14. Errores conocidos y resolución

- `No module named pytest`: activar el entorno virtual y ejecutar `python -m pip install -r requirements-task10.txt`.
- `Definí GP_DEMO_PASSWORD`: definir la variable mediante `read -r -s`, ejecutar la siembra y hacer `unset` inmediatamente.
- El seed debe leer diez filas de `integrantes.csv` porque un integrante puede tener varios usernames; la unicidad obligatoria es la del username.
- Un `503 TEMPORARILY_UNAVAILABLE` del portal puede ser una lectura asíncrona o una función que aún está propagando. Conservar el `request_id`, revisar CloudWatch y validar el estado en DynamoDB antes de repetir una acción que cambie saldo.
- La prueba de vencimiento y la de Diego dependen del reloj: capturar el estado `PENDIENTE` antes de que expire el plazo demo.
- Una prueba AWS que aparece `SKIPPED` no cuenta como ejecutada: revisar las variables `RUN_*` requeridas.
- El empaquetado falla con checkout sucio intencionalmente. Hacer commit o usar `--allow-dirty` sólo para una prueba local, nunca para la entrega autorizada.
- GitHub no acepta la contraseña de la cuenta para `git push`; usar un token personal con permisos mínimos, inyectarlo temporalmente y eliminarlo del entorno después.
