# Tarea 13: recaudación y administración

## Cálculo

`calcular_recaudacion(fecha_local)` acepta una fecha `AAAA-MM-DD` de Guatemala. El rango es `[06:00Z, 06:00Z del día siguiente)`. Se usan la fecha UTC de cada movimiento (`fecha`) y la emisión real de cada factura (`emitida_en`), no la fecha del paso original. `COBRO` suma; `DEVOLUCION` resta al peaje original y al día en que se devolvió. `RECARGA` no es ingreso. Las facturas emitidas se acumulan aparte como `por_cobrar_centavos`; no se suman al neto. **El pago de facturas no se implementa**, pues no forma parte del alcance solicitado.

Para la demo se hacen `Scan` consistentes y paginados de Peajes, Movimientos y Facturas **solo desde la ejecución de recaudación**. Un `Scan` consistente evita leer datos no confirmados por página, pero no crea una foto atómica entre páginas o tablas. Al recalcular el mismo día se reconcilian cobros y devoluciones registrados tarde. No se hace `Scan` desde el webhook. Para volumen real conviene mantener un índice/proyección por fecha efectiva de operación y peaje (incluido `emitida_en` para facturas) y usar `Query` por partición, más reconciliación de entradas tardías. Esto reduce lectura de toda la tabla, pero añade escritura/almacenamiento por índice y costo de conciliación.

Cada peaje tiene una fila estable `recaudacion_id` por fecha. Si los cuatro totales no cambian, el cálculo no escribe otra vez ni publica otro evento. Si cambian, reemplaza la fila con versión aumentada y crea `recaudacion.calculada` en Outbox en la misma transacción. EventBridge entrega el evento a la notificación **simulada** de finanzas (`destinatario=finanzas`, mensaje con reporte y fecha). No se envía correo real; el envío es opcional según los supuestos del curso. Las filas de distintos peajes se confirman individualmente: un error a mitad de jornada se corrige repitiendo el cálculo.

## Ejecución

El scheduler ejecuta `cron(0 6 * * ? *)` con zona `America/Guatemala` y ventana flexible `OFF`. Sin fecha se calcula el día local anterior. La ejecución manual invoca la Lambda desplegada:

```bash
python scripts/run_revenue.py --stack guatepass-dev --fecha AAAA-MM-DD
```

Repítela para verificar que no aumentan las versiones ni el número de notificaciones. La ruta `GET /revenue/{fecha}` y `GET /revenue` (ayer) son de consulta. `GET /tolls` también es de solo lectura. Ambas requieren sesión de administrador. El portal enlaza a `/backoffice/index.html` para ver los peajes y el cálculo guardado; publica los archivos estáticos con `python scripts/publish_portals.py --stack guatepass-dev --region us-east-1`.

```bash
python -m pytest tests/unit/test_dates.py tests/integration/test_revenue.py -q
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
```
