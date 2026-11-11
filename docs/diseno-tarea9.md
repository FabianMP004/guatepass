# Espera de recarga y factura — diseño de tarea 9

Un `paso.pendiente` entra en una cola SQS con DLQ; `iniciar` lee el paso con consistencia fuerte y arranca una ejecución Standard de nombre `paso_id`. La máquina consulta `vence_en` persistido, calcula segundos restantes y entra en `lambda:invoke.waitForTaskToken`. `registrar_token` guarda el token solamente en `Esperas` y comprueba después el estado; un cobro anterior dispara callback inmediato. `paso.cobrado` entra en otra cola y `completar` guarda primero señal terminal para tolerar entrega antes del token. El callback duplicado es inocuo.

Un timeout llama `vencer`. Una transacción de `money.py` cambia PENDIENTE por FACTURADO, retira el pendiente y crea factura+Outbox; una transacción de cobro que gana antes impide la factura por la condición de versión/estado. Para TAG Zona 10, Q4.50 + Q25.00 = Q29.50. La factura conserva NIT y nombre, no descuenta saldo y no produce movimiento COBRO. El reconciliador programado reintenta señales y revisa pendientes vencidos con lectura consistente de tabla base. No depende de TTL ni del estado de ejecución para afirmar que cobró.

La máquina procesa una factura vencida aunque el evento tarde; el límite del callback proviene del `vence_en` original, nunca del timeout SSM actual. La tabla demo es pequeña: el reconciliador usa Scan consistente y, al crecer, debe pasar a un cursor por shards. No registrar inputs de Step Functions ni tokens en CloudWatch. No exponer `Esperas` en API o portal.

Alternativas consideradas: un cron sin máquina no permite demostrar la espera con callback; una máquina por recarga rompería la identidad del paso. Standard por paso y token conserva la semántica del checkpoint y el control del vencimiento.
