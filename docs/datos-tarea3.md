# Modelo físico — tarea 3

El código de negocio debe escribir los atributos de índices de manera explícita; DynamoDB no los calcula. Todas las claves son String. Tablas cifradas, on-demand y PITR activado. PITR puede generar cargos; revisar presupuesto. No hay TTL automático: Idempotencia y Esperas no deben desaparecer mientras hay reintentos/plazos activos.

| Recurso lógico | PK | SK | Índices y campos derivados |
|---|---|---|---|
| UsuariosTable | placa | — | PorSub: sub/placa. Autorización normal usa GetItem consistente por placa + comparación sub. |
| PeajesTable | peaje_id | — | tarifa_base_centavos y modificadores de modalidad |
| TagsTable | tag_id | — | placa, activo. Vínculo activo en Usuarios; cambiar ambos en transacción para unicidad. |
| PasosTable | paso_id | — | PorPlaca: placa/orden; PorPeajeFecha: peaje_fecha/orden. |
| PendientesTable | placa | orden | orden=recibido_en#paso_id, UTC de ancho fijo; Query de tabla base con ConsistentRead=True. |
| MovimientosTable | movimiento_id | — | PorPlaca y PorPeajeFecha, ambos con orden. tipo COBRO/RECARGA/DEVOLUCION. |
| FacturasTable | factura_id | — | PorPlaca y PorPeajeFecha, ambos con orden. |
| DisputasTable | cobro_id | — | PorPlaca: placa/orden; PorEstado: estado/orden. disputa_id derivado; para resolver obtener cobro_id de contexto autorizado o mapa persistido, no escanear. |
| NotificacionesTable | notificacion_id | — | evento_id#destinatario |
| RecaudacionTable | recaudacion_id | — | fecha#peaje_id |
| OutboxTable | evento_id | — | PorPendiente: pendiente_shard/creado_en; Stream NEW_AND_OLD_IMAGES. |
| EsperasTable | espera_id | — | RECARGA#paso_id o DISPUTA#disputa_id; task token sólo servidor. |
| IdempotenciaTable | idempotency_id | — | usuario#operacion#clave; placa autorizada incluida en operación; fingerprint normalizado y status/cuerpo guardados. |

peaje_fecha=peaje_id#YYYY-MM-DD de Guatemala. orden en historiales=fecha_hora_UTC#id. Recibido_en se asigna una sola vez al ingreso; no usar timestamp de cámara para cambiar orden de pendientes. Estados de dinero y versión se modifican condicionalmente en tablas base. GSIs sólo para consulta/proyección: nunca decidir saldo, unicidad ni devolución mediante un GSI.

## Outbox

Una escritura de negocio futura inserta Outbox en la misma transacción que su estado. Campos:
- evento_id estable (PK), source="guatepass", detail-type.
- detail: **JSON serializado como String** del objeto detail del contrato, incluyendo grupo, carnet y evento_id. El adapter de eventos lo transmite sin generar otro ID.
- enviado: false; creado_en: UTC; pendiente_shard: PENDING#00 a PENDING#03 (seleccionar determinísticamente, por ejemplo int(sha256(evento_id).hexdigest(),16)%4).

El consumidor sólo recibe INSERT; marcar enviado es MODIFY, que el filtro excluye. Publicación al menos una vez: un crash entre PutEvents y marcado puede duplicar entrega. Los consumidores deduplican evento_id y claves de negocio. El recuperador cada 5 minutos reconsulta hasta 100 pendientes por shard, verifica tabla base consistentemente y reintenta. La DLQ recibe metadatos de lotes fallidos; Outbox conserva el evento recuperable. No borrar el evento al fallar. Tono añadirá reglas/colas de negocio, alarmas, permisos de publicación realtime e integraciones posteriores.

## Plazos

RecargaTimeoutSeconds y DisputaTimeoutSeconds son parámetros de despliegue (normal 86400; demo 120). SAM crea sus parámetros SSM String. src/common/deadlines.py lee SSM sin caché al crear el registro, calcula UTC y devuelve vence_en. Persistir vence_en en la misma unidad atómica de creación; en reintento leer el existente, no recalcular. Cambios futuros en SSM sólo afectan nuevos registros. Al añadir las máquinas, Wait debe usar TimestampPath: $.vence_en. Esta tarea no implementa pendientes/disputas ni Step Functions: la llamada al helper y su permiso ssm:GetParameter deberán integrarse con las funciones de negocio en sus tareas.

## Autorización

Grupos minúsculos e ID token reemplazan los nombres de la propuesta de tarea 2. Ejecutar scripts/align_contracts.py sobre docs/api.md para reflejarlo. Un Cognito válido no basta: /users verifica conductor+placa+sub de tabla; /passes exige operador y deriva el peaje del atributo administrado. No se expone ninguna Lambda Function URL. No permitir invocación lambda directa desde clientes.

AppSync Events permite suscripción Cognito sólo al canal exacto /peajes/{custom:peaje_id}; rechaza otro peaje y wildcard. Publicación IAM únicamente, todavía sin rol de negocio autorizado. No se implementa entrega de eventos de negocio al portal aquí. El JS autoritativo vive en template.yaml; docs/realtime-handler.js es copia de lectura, no cambiar sólo esa copia.

## Alcance y logs

GET /users/{placa}, GET /passes son sondas reales de autorización y lectura. /passes todavía no tiene cursor: si la consulta requiere segunda página devuelve 501 en vez de omitir datos silenciosamente. Implementar paginación firmada/ligada al usuario durante portales. POST /webhook/toll está protegido pero retorna 501 hasta la tarea 5. Sólo esos métodos están desplegados; los restantes siguen definidos en docs/api.md.

Cada Lambda tiene rol propio y logs con 14 días de retención; no registra cuerpos ni JWT. Permisos delimitados por tabla/índice/bus/cola; ListStreams usa * por descubrimiento. Logs de API Gateway y AppSync no están activados aún; Tono los incorporará con roles de servicio y retención en observabilidad. Los logs están cifrados por el servicio; S3 y DynamoDB declaran cifrado en plantilla. Todos los recursos usan referencias/parámetros; ningún ID real de cuenta/pool se copia a mano.

## Fuentes oficiales usadas

- https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-cognito-userpool.html
- https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-api-apiauth.html
- https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-enable-cognito-user-pool.html
- https://docs.aws.amazon.com/appsync/latest/eventapi/writing-event-handlers.html
- https://docs.aws.amazon.com/appsync/latest/eventapi/event-api-websocket-protocol.html
