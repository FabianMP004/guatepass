# Tarea 8 — recargas y pendientes

Base: main después de la tarea 7. Ejecutar desde la raíz del repositorio en CloudShell, región us-east-1. No sustituir CSV, parámetros del stack ni credenciales.

## Instalación y pruebas locales

Aplicar el instalador del ZIP; comprueba hashes de archivos existentes y no sobrescribe otros cambios. Si main difiere, revisar las diferencias antes de integrarlas.

```bash
cd ~/proyectos/guatepass
git switch fabian/base
git fetch origin
git merge origin/main
git status --short
unzip -q "$HOME/guatepass-tarea8.zip" -d "$HOME/guatepass-tarea8-parche"
python3.12 "$HOME/guatepass-tarea8-parche/instalar_parche_tarea8.py" --repo "$PWD" --apply
source .venv-task5/bin/activate
.venv-task5/bin/python -m pip install -r requirements-task8.txt
.venv-task5/bin/python -m pytest tests/integration/test_topup.py -v
.venv-task5/bin/python -m pytest -q
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
```

## AWS dev/demo

Confirmar identidad y parámetros del stack. Mantener `BillingConsumerEnabled=true` para la prueba automática; si estaba desactivado, revisar previamente la cola y habilitarlo con el despliegue habitual de la tarea 7. El instalador no despliega.

```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name guatepass-dev --region us-east-1 --query 'Stacks[0].StackStatus' --output text
.venv-task5/bin/python scripts/desplegar_tarea7.py --consumer true --apply
aws cloudformation describe-stacks --stack-name guatepass-dev --region us-east-1 --query 'Stacks[0].StackStatus' --output text
RUN_TOPUP_AWS=1 GP_STACK=guatepass-dev AWS_DEFAULT_REGION=us-east-1 .venv-task5/bin/python -m pytest tests/integration/test_topup_aws.py -v -s
```

El test real crea una placa aislada IT8-..., tres pendientes Q8, Q5, Q5 y dos recargas simultáneas Q4 + Q6. Comprueba estado eventual: un cobro Q8 y saldo Q2, sin segundos abonos por clave repetida. Conserva registros para evidencia. La invocación directa de Lambda con claims de prueba verifica transacciones y rutas internas; **no** sustituye una prueba de autorización en API Gateway. Revisar 401, 403 y CORS con una sesión Cognito real antes de exponer el portal.

Límite por operación Q10,000.00; monto API es texto decimal con máximo dos decimales. El navegador conserva su Idempotency-Key en reintentos y genera otra para acciones nuevas. Un paso vencido detiene la lista; la facturación lo resolverá en otra tarea. El reconciliador técnico de Esperas se ejecuta cada minuto; los eventos de Outbox se publican por el mecanismo de la tarea 5.

## Entrega

Tras registrar salidas reales en docs/historico/verificaciones/VERIFICACION-TAREA8.md:

```bash
git add template.yaml src/common/money.py src/pasos/cobrar.py src/pasos/pendientes.py src/usuarios/recargar.py tests/integration/test_topup.py tests/integration/test_topup_aws.py docs/api.md docs/eventos-v2.md requirements-task8.txt LEEME-TAREA8.md docs/historico/verificaciones/VERIFICACION-TAREA8.md
git diff --cached --check
git commit -m "Implementar recargas idempotentes y drenaje FIFO de pendientes"
git push origin fabian/base
```

Abrir PR hacia main y solicitar revisión de Cynthia o Tono.
