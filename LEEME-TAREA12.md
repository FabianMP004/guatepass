# Tarea 12: disputas y devolución única

Implementación: `src/disputas/abrir.py`, `listar.py`, `resolver.py`, `devolucion.py`, `api.py`, máquina Standard `statemachines/espera_disputa.asl.json`, rutas y permisos SAM, portal conductor y atención.

## Reglas

- Solo un movimiento `COBRO` propio puede disputarse; la clave `cobro_id` de DisputasTable impide una segunda disputa. El ítem `REF#<disputa_id>` permite resolver desde las rutas por ID.
- La apertura guarda disputa, referencia y `disputa.abierta` en una transacción. El plazo leído de SSM queda guardado en `vence_en`. La ejecución Standard utiliza el ID estable de disputa.
- Una decisión se persiste condicionalmente antes de su aplicación. El timeout la consulta antes de aprobar automáticamente; nunca sustituye una decisión manual ya guardada.
- La aprobación confirma en una sola transacción estado final, suma de saldo, incremento de versión, movimiento único `DEVOLUCION` y dos eventos Outbox. Un rechazo no toca el saldo. El consumidor existente procesa `saldo.devuelto` y reintenta pagar pendientes.
- El historial de pagos muestra tanto `COBRO` como `DEVOLUCION`; la devolución sigue visible aunque el saldo resultante se gaste después.
- El token permanece en Esperas; una tarea programada revisa disputas abiertas y callbacks pendientes cada minuto.

## Antes del despliegue

Integrar estos archivos en la rama elegida sin sobrescribir cambios concurrentes. Revisar `sam validate --lint --template-file template.yaml --region us-east-1`; luego `sam build --template-file template.yaml` y desplegar en un ambiente de prueba mediante el procedimiento del proyecto. SAM debe declarar `DisputeApiFunction`, `EsperaDisputa` y `DisputeReconcileFunction`.

## Pruebas locales

```bash
PYTHONPATH=src python -m unittest discover -s tests/unit -p test_devolucion.py -v
PYTHONPATH=src python -m unittest discover -s tests/unit -p test_disputes_local.py -v
python -m pytest tests/unit -q
node --test portales/live.test.mjs
```

Las pruebas AWS son optativas; usan cobros reales del **ambiente de pruebas**. No reutilizar la misma ID para escenarios distintos:

```bash
RUN_DISPUTES_AWS=1 GP_STACK=guatepass-dev \
DISPUTE_COBRO_ID=<COBRO_DE_PRUEBA> \
DISPUTE_REJECT_COBRO_ID=<OTRO_COBRO> \
DISPUTE_EXPIRE_COBRO_ID=<TERCER_COBRO> \
DISPUTE_DRIVER_SUB=<SUB_DEL_CONDUCTOR> DISPUTE_PLACA=<PLACA> \
DISPUTE_CARNET=<CARNET> \
python -m pytest tests/integration/test_disputes.py -v
```

Para vencimiento usar el parámetro SSM de demo (p. ej. 120 segundos) **antes** de abrir la tercera disputa. Confirmar en DynamoDB que el movimiento de devolución persiste aunque luego se use el saldo para cobrar pendientes; comprobar Step Functions, Outbox y portal con dos disputas diferentes: una rechazada y otra aprobada por vencimiento. Ejecutar nuevamente el mismo resultado no debe crear otro movimiento ni otro crédito.

## Advertencia de integración

La suite AWS necesita credenciales, despliegue y cobros frescos de prueba. Este ZIP no trae configuración de credenciales ni realiza cambios en AWS. La tarea se cierra solo después de `sam validate`, build, despliegue y pruebas reales con sus evidencias.
