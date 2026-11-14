# Guion de demostración de la tarea 15

## Preparación

```bash
cd /home/cloudshell-user/proyectos/guatepass
source .venv-task11/bin/activate
export AWS_DEFAULT_REGION=us-east-1
export GP_STACK=guatepass-dev
```

Confirmar que el stack esté disponible:

```bash
aws cloudformation describe-stacks \
  --stack-name guatepass-dev \
  --region us-east-1 \
  --query "Stacks[0].StackStatus" \
  --output text
```

Debe mostrar `UPDATE_COMPLETE`.

Abrir el portal:

```text
https://d3j87in9jsupwd.cloudfront.net
```

Usar ventanas separadas para conductor, operador y administrador. No mostrar contraseñas ni API keys durante la demostración.

## Orden recomendado

1. Juan inicia sesión, verifica `TAG-005`, envía un paso y muestra Q4.50 cobrado.
2. Andrés inicia con saldo bajo, muestra el pendiente, recarga Q20.00 y muestra el cobro posterior.
3. Se muestra la factura de Andrés después del vencimiento de 120 segundos.
4. Se envían los casos de Rosa, Carlos y `P-000XXX`. Rosa se muestra desde operador/backend porque no tiene usuario de conductor.
5. María muestra dos cobros y abre dos disputas. Atención rechaza una manualmente; la otra se deja vencer para mostrar la aprobación automática y devolución.
6. Diego muestra un cobro APP y un pendiente APP con saldo final Q0.00. La evidencia del pendiente debe capturarse antes de los 120 segundos.
7. El administrador consulta el reporte del día `2026-11-14`.
8. María recibe tres veces el mismo aviso TAG. Se muestra que existe un solo paso, un solo movimiento y saldo Q245.50.

## Verificación automatizada

```bash
RUN_FLOWS_AWS=1 \
GP_STACK=guatepass-dev \
AWS_DEFAULT_REGION=us-east-1 \
python -m pytest tests/integration/test_flows.py -v
```

Pruebas unitarias:

```bash
python -m pytest tests/unit -v
```

Suite de integración:

```bash
python -m pytest tests/integration -v
```

## Puntos que deben explicarse

- Las lecturas de portal pueden ser asíncronas; la evidencia definitiva se toma de DynamoDB, logs y Outbox.
- Las facturas de placas no registradas no tienen portal de conductor.
- Las devoluciones son movimientos contables independientes y no borran el cobro original.
- Los reintentos del mismo aviso no crean un segundo paso ni un segundo cobro.
- Los saldos no se restauraron a escondidas; cada recarga y paso de preparación está documentado.
