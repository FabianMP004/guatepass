# Verificación — tarea 6

## Preparación comprobada

- 34 pruebas sin SDK aprobadas mediante unittest: 25 previas y 9 del catálogo v2.
- Sintaxis de módulos Python comprobada con compileall.
- YAML parseado; referencias explícitas revisadas.
- Visibility 180 s, timeout 30 s y ventana 0 comprobados estructuralmente.
- Colas con retención de 4 días, DLQ de 14 días y Archive de 30 días.
- Permisos de envío EventBridge restringidos por regla y cuenta.
- Webhook conserva 1024 MB.

## Pendiente en CloudShell

- Ejecutar suite pytest local: tests/unit, test_outbox.py y test_routing.py.
  No se ejecutó aquí por indisponibilidad de boto3/pytest/Moto y del índice de paquetes.
- sam validate --lint y sam build.
- Despliegue UPDATE_COMPLETE.
- Patrones reales, notificación real simulada persistida y duplicado sin cambio.
- Mensaje malformado alcanza NotificacionesDLQ mediante redrive real.
- Reejecutar test_auth.py si corresponde (expectativa antigua 501 corregida a 400
  para cuerpo vacío con clave válida).
- Revisión del catálogo v2, migración y tratamiento de contacto en invitaciones.

## Dependencias posteriores y límites

- Consumidores de pasos/registro, tiempo real y esperas se conectan en sus tareas.
- Cobro único eventual pendiente del consumidor de cobros.
- Fallo real de entrega EventBridge->SQS no inducido; DLQ y políticas declaradas.
- Replay documentado, no ejecutado automáticamente.
- No se envía correo ni SMS; solo se registra la simulación.

Completar este documento con evidencia real después de ejecutar, sin marcar
como aprobada una prueba omitida ni incluir secretos/contactos personales.

### Evidencia de redrive real a DLQ

Confirmado: el mensaje malformado original llegó a NotificacionesDLQ.

Marcador:
malformed-routing-test-d1943d10dcdb48e1803373fbdc0392c2

Los logs mostraron reintentos del mismo mensaje separados por
180 segundos. Después se encontró el marcador original en la DLQ.

La ejecución inicial de pytest se interrumpió por un error al renovar
credenciales de CloudShell. La verificación se completó posteriormente
consultando la DLQ; no se reporta esa ejecución de pytest como aprobada.

No se eliminó ningún mensaje.
