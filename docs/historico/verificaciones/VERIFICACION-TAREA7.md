# Verificación tarea 7

## Realizado en la preparación del paquete

- [x] Inspección del ZIP base de main y contratos de tareas anteriores.
- [x] 41 comprobaciones con unittest: 3 tarifas, 4 infraestructura,
      15 contratos, 10 autorización local y 9 catálogo v2.
- [x] Revisión de código independiente; corregido reintento de DELETE que podía
      cambiar de tag objetivo y separado cobro de pendientes de avisos repetidos.
- [x] Compilación sintáctica de fuentes, scripts y tests.
- [x] Suite pytest/Moto ejecutada en CloudShell: 27 pruebas específicas aprobadas;
      suite completa con 99 aprobadas y 22 omitidas (pruebas AWS optativas).
- [x] sam validate --lint y sam build aprobados en CloudShell.

## CloudShell / AWS — completar con evidencia real

- [x] Tests de tarifas y wallet: 27 aprobados en 5.55 s
- [x] Suite completa: 99 aprobados, 22 omitidos; pruebas AWS optativas
- [x] Asignaciones reales de todos los conductores revisadas y sincronizadas.
- [x] SAM validado y construido sin errores.
- [x] Primer despliegue UPDATE_COMPLETE con BillingConsumerEnabled=false.
- [x] AWS: dos pasos concurrentes, un cobro Q5, un pendiente, saldo Q0.
- [x] AWS: repetir entregas no cambia saldo, movimientos, estado ni vencimiento.
- [x] API tags: propia permitida, ajena/rol incorrecto 403, sin token 401.
- [x] API tags: asociación y desactivación repetibles, saldo intacto.
- [x] Avisos acumulados revisados; consumidor activado por SAM, mapping Enabled.
- [x] Webhook → Outbox → bus → SQS → Cobrar confirmado en AWS.
- [ ] Evidencia guardada, revisión de compañero y PR fusionado.

## Prueba de fallo de publicación y cobro único

- [x] Local: publicación aceptada + fallo de marcado + republicación produce un cobro.
- [ ] AWS: repetir prueba de fallo de publicadora y recuperación con consumidor ya activo,
      comprobar un paso y un movimiento usando claves base.

La tarea 5 documenta recuperación real previa; no confundir esa evidencia con una
nueva prueba end-to-end del cobro. El test AWS de esta entrega invoca CobrarFunction
en paralelo y comprueba DynamoDB real; no simula un fallo real de EventBridge.

## Datos de la comprobación real

- Fixture IT7-2b9642b3fdf34ce497998dcef1ccb5b7: un cobro Q5,
  un pendiente, saldo Q0; duplicados sin otro descuento. Test AWS aprobado.
- API de tags: 1 prueba aprobada; 15 no seleccionadas por `-k tag_routes`.
- Consumidor de cobros: Enabled, con ReportBatchItemFailures.
- Webhook: tres intentos devolvieron
  paso_5fb5ca52d1910ce8f580a91c646c794e18e33aa19f443f3fe3a087c9ab1e95d5.
- Paso COBRADO por Q5, modalidad APP; exactamente un movimiento.
  Saldo observado de María: Q245.00.
- Evento paso.cobrado en Outbox:
  evento_8050dbcc9f52dfdf0057106d8b5d2bcb661bf5f60ebf3da4d853c92e08a45186;
  enviado=true.
- Cinco pasos antiguos permanecían REGISTRADO y cola-pasos tenía
  cero mensajes. Su recuperación sigue pendiente.

## Pendientes de tareas posteriores

- Recargas/devoluciones y cobro ordenado de pendientes (sin recalcular tarifa).
- Inicio/cierre de esperas y facturación al vencer, usando vence_en persistido.
- Registro externo, tiempo real y resto de consumidores del catálogo.
