# Tarea 9 — espera de recarga y factura por vencimiento

Base: `main` actualizado después de tarea 8. Entorno: CloudShell, Python 3.12, `guatepass-dev` en us-east-1. El paquete aplica solo las diferencias de tarea 9; el instalador verifica hashes y no toca CSV ni secretos.

## Comprobación local

```bash
cd ~/proyectos/guatepass
git switch fabian/base
git fetch origin
git merge origin/main
git status --short
unzip -q "$HOME/guatepass-tarea9.zip" -d "$HOME/guatepass-tarea9-parche"
python3.12 "$HOME/guatepass-tarea9-parche/instalar_parche_tarea9.py" --repo "$PWD" --apply
source .venv-task5/bin/activate
.venv-task5/bin/python -m pip install -r requirements-task9.txt
.venv-task5/bin/python -m pytest tests/integration/test_wait_topup.py -v
.venv-task5/bin/python -m pytest -q
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
```

Si una prueba o `sam validate` falla, detenerse y compartir el error antes de desplegar. Los tests AWS se omiten en la suite normal.

## Despliegue y verificación AWS

El despliegue conserva los parámetros actuales (incluido timeout demo) y mantiene el consumidor habilitado. Puede haber pendientes anteriores que el reconciliador facture inmediatamente si `vence_en` ya pasó; revisar su estado antes de desplegar. No limpiar las colas ni restablecer saldos.

```bash
aws sts get-caller-identity
.venv-task5/bin/python scripts/desplegar_tarea7.py --consumer true --apply
aws cloudformation describe-stacks --stack-name guatepass-dev --region us-east-1 --query 'Stacks[0].StackStatus' --output text
RUN_WAIT_AWS=1 GP_STACK=guatepass-dev AWS_DEFAULT_REGION=us-east-1 \
.venv-task5/bin/python -m pytest tests/integration/test_wait_topup_aws.py -v -s
```

La prueba AWS usa fixtures con identificador IT9-... y genera dos execution ARNs: uno RUNNING → SUCCEEDED con paso COBRADO por Q4.50; otro RUNNING → SUCCEEDED con paso FACTURADO Q29.50 y saldo cero. Los deja identificados como evidencia; no modifica el saldo de los siete clientes reales. Abrir cada ARN en Step Functions → Graph view, observar RUNNING y SUCCEEDED; verificar `estado` y factura en DynamoDB además del Graph. La ejecución puede tardar hasta ~90 segundos; no cerrar CloudShell mientras corre. La prueba invoca Lambda de recarga directamente con claims sintéticos: todavía no sustituye la prueba real de autenticación por API Gateway.

El reconciliador escanea la tabla base de pasos en este entorno pequeño y reintenta cierres. Nunca enviar tokens a logs, frontend, correo ni capturas. Un error de ejecución FAILED requiere revisar historial sin exponer token y redrive autorizado; no reusar el mismo nombre con distinta entrada. Los eventos de Outbox pueden repetirse.

## Guardar cambios

Actualizar VERIFICACION-TAREA9.md solo con salidas observadas. Tras revisión de Cynthia o Tono:

```bash
git add template.yaml statemachines/espera_recarga.asl.json src/common/money.py src/pasos/facturar.py src/flujos tests/integration/test_wait_topup.py tests/integration/test_wait_topup_aws.py docs/diseno-tarea9.md docs/eventos-v2.md requirements-task9.txt LEEME-TAREA9.md VERIFICACION-TAREA9.md
git diff --cached --check
git commit -m "Implementar espera de recarga y factura por vencimiento"
git push origin fabian/base
```

Crear PR `fabian/base` → `main` y pedir revisión antes de fusionarlo.
