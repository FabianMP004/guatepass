# GuatePass — contrato API v1.0.0

Estado: propuesta implementable para revisión conjunta. Fecha: 2026-11-06.
Fuente de requisitos: Guia-Proyecto-Final-GuatePass, RF-01–12 y RNF-01–06; rutas provistas en tarea 2. Los nombres y decisiones de interfaz siguientes son acuerdos propuestos, no texto literal del curso. No se ha conectado a un repositorio ni desplegado una API.

## Convenciones comunes

- HTTPS; Content-Type: application/json; UTF-8; rutas relativas a la URL del despliegue. La versión de contrato se lleva en Git, no se agrega /v1 sin acuerdo.
- Respuesta exitosa: {codigo, mensaje, request_id, data}. Error: {codigo, mensaje, request_id}; nunca stack traces ni secretos. request_id lo genera el servidor. Un replay de recarga devuelve los mismos datos de recarga; request_id identifica cada solicitud recibida.
- Campos desconocidos de entrada: 400 VALIDATION_ERROR. Fechas de negocio YYYY-MM-DD en America/Guatemala; timestamps RFC3339 con zona, normalizados a UTC. La cámara debe enviar UTC.
- Dinero en JSON: cadenas decimales de quetzales con dos posiciones, por ejemplo "20.00". Internamente enteros en centavos. Recarga: monto positivo, hasta 999999999.99 (límite técnico propuesto), máximo dos decimales. El helper acepta también números para migración interna; el handler debe exigir cadena en la API v1 y parsear decimales sin pérdida.
- JWT: Authorization: <id_token>. Validar firma, emisor, expiración, token_use=id y aud del app client autorizado antes de usar sub. Roles propuestos: conductor, operador, atencion, administrador. Una cuenta demo tiene un rol. El backend resuelve placa/peaje desde el sub validado y datos sembrados. El rol viene del contexto autenticado validado, jamás del body/query.
- Cámara: x-api-key configurada fuera del código; validación efectiva en el ingreso. Elegir implementación de API Gateway/authorizer en tarea 3; no suponer que cualquier tipo de API valida este encabezado automáticamente.
- No se aceptan carnet, rol, saldo, monto de tarifa ni peaje de operador como fuentes de autorización. El monto de recarga es una entrada validada, no una autorización para cobrar otra cuenta. La placa del path se compara con la asociación del sub.
- Errores comunes en TODAS las rutas: 400 VALIDATION_ERROR; 401 UNAUTHORIZED (autenticación ausente/inválida); 403 FORBIDDEN (rol/propiedad); 429 RATE_LIMITED; 500 INTERNAL_ERROR; 503 TEMPORARILY_UNAVAILABLE. Los recursos ajenos retornan 403 sin revelar su contenido. No reintentar 400/403/409 automáticamente; 429/503 con espera y misma clave para la misma recarga.
- Listas: limit entero 1–100 (default 50), cursor opaco opcional, resultado {items, next_cursor}; next_cursor=null al finalizar. Cursor ligado a identidad, filtros y orden; rechazar alterado o de otra consulta con 400 INVALID_CURSOR. Orden: creado_en descendente y ID como desempate; pasos del operador por timestamp e ID. La paginación no promete una instantánea frente a escrituras concurrentes.
- Las lecturas asíncronas pueden estar atrasadas: la respuesta del webhook acredita recepción durable, no cobro completado. Historial y saldo se refrescan al recibir eventos o mediante consulta.

Ejemplo de error común:
```json
{
  "codigo": "FORBIDDEN",
  "mensaje": "No tiene acceso a este recurso.",
  "request_id": "req-ejemplo"
}
```

## POST /webhook/toll

Acceso: Cámara; x-api-key.

Petición: 
```json
{
  "placa": "P123ABC",
  "peaje_id": "T1",
  "timestamp": "2026-11-06T12:00:00Z",
  "tag_id": null
}
```

Respuesta HTTP 202:
```json
{
  "codigo": "OK",
  "mensaje": "Paso recibido.",
  "request_id": "req-ejemplo",
  "data": {
    "paso_id": "paso_hash"
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

404 TOLL_NOT_FOUND. Repetición: 202 y mismo paso_id. Placa+peaje+timestamp determinan identidad; tag_id no cambia el hash. Un duplicado conserva los datos del primer aviso. Confirmar persistencia antes de responder; objetivo <1 segundo.

## GET /users/{placa}

Acceso: conductor propio.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "placa": "P123ABC",
    "saldo": "20.00",
    "moneda": "GTQ"
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

404 USER_NOT_FOUND. Sin body.

## POST /users/{placa}/topup

Acceso: conductor propio; Idempotency-Key obligatorio. Solo se acepta monto como texto decimal positivo, máximo dos decimales y Q10,000.00 por recarga.

Petición: 
```json
{
  "monto": "20.00"
}
```

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "recarga_id": "recarga_hash",
    "monto": "20.00",
    "saldo_despues_recarga": "20.00",
    "moneda": "GTQ"
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

400 IDEMPOTENCY_KEY_REQUIRED; 409 IDEMPOTENCY_CONFLICT; 409 IDEMPOTENCY_IN_PROGRESS (reintentar misma clave tras espera). El saldo mostrado es el inmediatamente posterior al abono; cobros asíncronos pueden cambiarlo después.

## GET /history/payments/{placa}

Acceso: conductor propio.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "items": [
      {
        "cobro_id": "cobro_hash",
        "paso_id": "paso_hash",
        "peaje_id": "T1",
        "monto": "4.50",
        "moneda": "GTQ",
        "creado_en": "2026-11-06T12:00:01Z",
        "devuelto": false
      }
    ],
    "next_cursor": null
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

Paginación común. devuelto se deriva de devolución confirmada. El cobro original permanece.

## GET /history/invoices/{placa}

Acceso: conductor propio.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "items": [
      {
        "factura_id": "factura_hash",
        "paso_id": "paso_hash",
        "numero": "GP-factura_hash",
        "fecha": "2026-11-06",
        "monto": "29.50",
        "concepto": "PLAZO_VENCIDO",
        "estado": "POR_COBRAR",
        "contribuyente": {
          "nombre": "Usuario demo",
          "nit": "NIT_SEMILLA"
        }
      }
    ],
    "next_cursor": null
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

Paginación común. Conceptos PLAZO_VENCIDO y NO_REGISTRADO. No incluye un flujo de pago de factura.

## POST /users/{placa}/tag

Acceso: conductor propio.

Petición: 
```json
{
  "tag_id": "TAG-001"
}
```

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "tag_id": "TAG-001",
    "placa": "P123ABC",
    "activo": true
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

409 TAG_IN_USE si está asociado activamente a otra placa; 409 ACTIVE_TAG_EXISTS si esta placa ya tiene otro activo. Misma asociación activa: 200 sin nuevo evento. Validar existencia/asociación en servidor.

## GET /users/{placa}/tag

Acceso: conductor propio.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "tag": {
      "tag_id": "TAG-001",
      "activo": true
    }
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

Sin tag activo: data={"tag":null}. Sin body.

## DELETE /users/{placa}/tag

Acceso: conductor propio.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "tag_id": "TAG-001",
    "activo": false
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

Repetir no produce otro evento. Si nunca existió, tag_id=null y activo=false. Desactivar no borra historial.

## POST /disputes

Acceso: conductor; cobro propio.

Petición: 
```json
{
  "cobro_id": "cobro_hash",
  "motivo": "No reconozco este paso."
}
```

Respuesta HTTP 201:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "disputa_id": "disputa_hash",
    "cobro_id": "cobro_hash",
    "estado": "ABIERTA",
    "vence_en": "2026-11-07T12:00:00Z"
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

404 PAYMENT_NOT_FOUND; 409 DISPUTE_EXISTS. Una disputa por cobro, aun cerrada. Motivo 1–1000 caracteres tras trim. Sólo cobros de saldo, no facturas.

## GET /disputes

Acceso: conductor: suyas; atencion: abiertas.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "items": [
      {
        "disputa_id": "disputa_hash",
        "cobro_id": "cobro_hash",
        "estado": "ABIERTA",
        "motivo": "No reconozco este paso.",
        "vence_en": "2026-11-07T12:00:00Z"
      }
    ],
    "next_cursor": null
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

Paginación común. Identidad y filtros impuestos por servidor; no acepta placa de otro usuario.

## POST /disputes/{id}/resolve

Acceso: atencion.

Petición: 
```json
{
  "decision": "APROBAR",
  "motivo": "Se verificó un cobro incorrecto."
}
```

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "disputa_id": "disputa_hash",
    "estado": "APROBADA",
    "devolucion_id": "devolucion_hash"
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

decision APROBAR o RECHAZAR, motivo obligatorio 1–1000. Rechazo devuelve estado RECHAZADA y devolucion_id=null. 404 DISPUTE_NOT_FOUND; 409 DISPUTE_ALREADY_RESOLVED. Competir con vencimiento mediante una transición atómica. APROBADA sólo cuando devolución registrada; si implementación asíncrona, proponer un nuevo contrato antes de cambiar respuesta.

## GET /passes?fecha=YYYY-MM-DD

Acceso: operador; peaje derivado de identidad.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "fecha": "2026-11-06",
    "peaje_id": "T1",
    "items": [
      {
        "paso_id": "paso_hash",
        "placa": "P123ABC",
        "timestamp": "2026-11-06T12:00:00Z",
        "estado": "COBRADO",
        "monto": "4.50"
      }
    ],
    "next_cursor": null
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

fecha opcional: hoy en Guatemala. Paginación común. No acepta peaje_id. Antes de determinar tarifa monto=null. No exponer NIT/contacto del dueño.

## GET /tolls

Acceso: administrador.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "items": [
      {
        "peaje_id": "T1",
        "nombre": "Peaje demo",
        "tarifa_base": "5.00",
        "descuento_tag_porcentaje": "10.00",
        "recargo_no_registrado_porcentaje": "20.00"
      }
    ],
    "next_cursor": null
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

Valores de ejemplo, no semillas verificadas. Paginación común. Sólo lectura: los cambios de tarifas no tienen ruta autorizada en tarea 2.

## GET /revenue/{fecha}

Acceso: administrador.

Petición: sin cuerpo JSON; parámetros indicados en ruta y convenciones.

Respuesta HTTP 200:
```json
{
  "codigo": "OK",
  "mensaje": "Operación completada.",
  "request_id": "req-ejemplo",
  "data": {
    "fecha": "2026-11-06",
    "calculado_en": "2026-11-06T18:00:00Z",
    "items": [
      {
        "peaje_id": "T1",
        "cobrado": "100.00",
        "devuelto": "4.50",
        "neto": "95.50",
        "por_cobrar": "29.50"
      }
    ]
  }
}
```

Errores: todos los comunes y los específicos siguientes. Cada error conserva exactamente {codigo, mensaje, request_id}.

400 INVALID_DATE; 404 REPORT_NOT_READY. Es lectura del reporte calculado, no dispara cálculo. Cada movimiento cuenta en su fecha local; facturas no se suman al neto. La ejecución manual por fecha será un script interno de tarea posterior.

## Idempotencia de recarga

Idempotency-Key: UUID generado por el navegador una sola vez al iniciar la acción. Conservar hasta conocer el resultado definitivo, incluso tras timeout o recarga de página; acción nueva = clave nueva. No guardar tokens de autenticación junto con la clave.
Clave de almacenamiento: (sub autenticado, "POST /users/{placa}/topup", placa autorizada, Idempotency-Key). Comparar monto normalizado en centavos: "20.0" y "20.00" equivalen si la entrada se normaliza. Guardar el resultado de recarga en la misma transacción que el crédito y devolverlo con HTTP 200. Misma clave y monto: devolver resultado guardado sin abonar ni emitir otro evento. Distinto monto: 409 IDEMPOTENCY_CONFLICT. Reservar clave y abonar mediante operación atómica/transacción; concurrencia no se resuelve con un diccionario Python. Un registro en curso no se toma como recarga terminada. Conservar registros durante todo el proyecto; TTL sólo después de definir ventana de reintentos.

## Identificadores

Algoritmo común stable_id(kind,*parts): SHA-256 del JSON UTF-8 [kind,*parts], separadores compactos y sin escape ASCII; resultado kind_ + 64 hex. No truncar. No usar hash() de Python.
- paso_id: kind=paso, placa strip/upper, peaje_id strip, timestamp convertido UTC con seis microsegundos. Conservar peaje_id como cadena, incluidos ceros iniciales.
- cobro_id: kind=cobro, paso_id. Un cobro por paso.
- factura_id: kind=factura, paso_id, concepto (PLAZO_VENCIDO o NO_REGISTRADO).
- disputa_id: kind=disputa, cobro_id. Una disputa total por cobro.
- devolucion_id: kind=devolucion, disputa_id. Una devolución por disputa.
- recarga_id: kind=recarga, sub, placa normalizada, Idempotency-Key.
- evento_id: kind=evento, detail-type, aggregate_id, aggregate_version (entero monotónico persistido). Reintentar publicación usa ID, versión y occurred_at originales; NO generar otra transición.

## Estados del paso

| Desde | Hacia permitidos |
|---|---|
| REGISTRADO | COBRADO, PENDIENTE, NO_REGISTRADO |
| PENDIENTE | COBRADO, FACTURADO |
| NO_REGISTRADO | FACTURADO, NO_IDENTIFICADO |
| COBRADO | ninguno |
| FACTURADO | ninguno |
| NO_IDENTIFICADO | ninguno |

Error del registro externo: conservar NO_REGISTRADO y reintentar; nunca convertir una falla técnica en NO_IDENTIFICADO. Este último requiere respuesta definitiva de placa inexistente. Repetir el webhook no reinicia estados. Una devolución no revierte el estado COBRADO; crea un movimiento contable independiente.
Transiciones protegidas por condición sobre estado/versión al persistir. Los helpers son validación de contrato, no reemplazan esa condición.

## Congelación y revisión

Fabián revisa identidad, códigos y persistencia; Cynthia revisa montos, estados y carreras; Tono revisa eventos, consumidores y fecha local. Cada integrante explica un ejemplo. Registrar aceptación en el PR y etiquetar contracts-v1.0.0 después de integrar. Campos opcionales nuevos: versión menor; quitar/renombrar/cambiar significado: versión mayor y migración acordada. No se ha creado commit/tag desde este paquete.

## Ajuste de autenticación — tarea 3

API Gateway REST con autorizador Cognito valida el ID token del app client (aud), sin scopes OAuth en los métodos. Los grupos canónicos son conductor, operador, atencion y administrador. Los atributos custom:placa, custom:peaje_id y custom:carnet se escriben administrativamente y son sólo lectura para el app client. Cada Lambda verifica el rol y la propiedad. La tabla Usuarios se consulta por placa con lectura consistente y también se verifica sub. Esta instrucción reemplaza el uso de access token de la propuesta inicial.
