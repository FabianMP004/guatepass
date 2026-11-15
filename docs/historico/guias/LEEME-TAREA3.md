# GuatePass — tarea 3

Base de infraestructura para integrar en el repositorio existente. No ha sido desplegada en tu cuenta. Mantener archivos de tarea 2, especialmente models.py y test_contracts.py. Este ZIP no contiene credenciales ni semillas reales.

## Integrar

1. Subir ZIP a CloudShell. Extraer fuera del repositorio: mkdir -p ~/tarea3-revision ; unzip ~/guatepass-tarea3.zip -d ~/tarea3-revision.
2. cd ~/proyectos/guatepass ; git status ; git switch fabian/base. Resolver cambios pendientes antes de copiar.
3. Si ya existe template.yaml, compararlo primero: esta es una plantilla base completa, NO un parche para un stack ya desarrollado. No sustituir trabajo de Tono a ciegas.
4. Copiar con revisión: cp -ri ~/tarea3-revision/guatepass-tarea3/. .
5. Activar Python 3.12: source .venv/bin/activate. Si falta: sudo dnf install -y python3.12 ; python3.12 -m venv .venv.
6. python -m pip install -r requirements-task3.txt. Consolidar dependencias con requirements-dev.txt al integrar, manteniendo versiones compatibles con tarea 2.
7. python scripts/align_contracts.py ; git diff docs/api.md.
8. Añadir portales/config.json al .gitignore; sus IDs son públicos pero se generan por ambiente, no se mantienen a mano. Revisar que .aws-sam/, .venv/ y secretos también están ignorados.
9. python -m pytest tests/unit -q. En conjunto con tarea 2 deben pasar las 15 pruebas de contratos más las 10 nuevas.

## Validar y desplegar

Comprobar aws sts get-caller-identity, región us-east-1 y Python 3.12. Evitar despliegues simultáneos.

```bash
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
sam deploy --guided --stack-name guatepass-dev --region us-east-1 --capabilities CAPABILITY_IAM
```

Responder Environment=dev, OwnerCarnet=TU_CARNE_REAL, RecargaTimeoutSeconds=120 y DisputaTimeoutSeconds=120 para ensayo. Los defaults son 86400 para operación normal. Confirm changes=Y, Allow SAM CLI IAM role creation=Y si pregunta, Disable rollback=N, Save arguments=Y. El aviso de WebhookFunction sin autorización Cognito es esperado: usa API key; confirmar sólo tras verificar ApiKeyRequired=True. Guardar configuración samconfig.toml del proyecto, no reutilizar la de guatepass-preflight. El stack es distinto a la prueba CloudFront de tarea 1.

```bash
aws cloudformation describe-stacks --stack-name guatepass-dev --region us-east-1 --query 'Stacks[0].StackStatus' --output text
aws cloudformation describe-stacks --stack-name guatepass-dev --region us-east-1 --query 'Stacks[0].Outputs' --output table
```

Seguir sólo con CREATE_COMPLETE o UPDATE_COMPLETE. Si falla, revisar CloudFormation Events y el primer fallo causal; no reconstruir recursos a mano. No dar por aprobada una validación no ejecutada.

## Hosting

```bash
python scripts/publish_portal.py --stack guatepass-dev --region us-east-1
```

Genera config.json desde Outputs y carga index.html/config.json al bucket privado. Abrir PortalUrl cuando la distribución esté desplegada. La página es un aviso de preparación, todavía no login ni portales funcionales. Nunca incluye la API key de cámara. S3 no habilita website público. No se copia el bucket de prueba anterior.

## Pruebas reales

```bash
RUN_AWS_INTEGRATION=1 GP_STACK=guatepass-dev AWS_REGION=us-east-1 python -m pytest tests/integration/test_auth.py -v
```

La suite crea dos usuarios de prueba efímeros (conductor y operador) y un saldo de Q20 en Usuarios, sólo en dev/demo. Esto es carga de datos de prueba mediante APIs, no infraestructura manual. Al terminar los elimina en finally. Si se interrumpe el proceso, pueden quedar usuarios con prefijo it- y una fila IT-: revisar y limpiar sólo esos IDs de la ejecución. No toca semillas reales. Contraseñas/tokens sólo en memoria. Se necesitan permisos IAM de CloudFormation lectura, Cognito administración de usuarios y autenticación, GetApiKey y escritura de esa tabla; usar el usuario de desarrollo ya autorizado.

15 pruebas de integración: 401 sin token, 200 propio, 403 ajeno/rol, access token rechazado, operador propio, selector de peaje bloqueado, OPTIONS libre, webhook sin clave bloqueado/con clave 501, CORS en errores, atributo custom protegido, suscripción propia permitida, otra y wildcard denegadas. Si las pruebas aparecen SKIPPED no cuentan como aprobadas. Reintentar después de propagación sólo si hubo evidencia de ese retraso; conservar el primer error para diagnóstico.

## Cerrar tarea

- Evidencia de sam validate --lint, sam build, stack COMPLETE, Outputs, sitio accesible y pruebas reales satisfactorias.
- Cynthia revisa plazos/datos; Tono revisa outbox, bus y canales; Fabián integra. No afirmar que hay cobros o frontend funcional todavía.
- git add explícito de template.yaml, src/common/auth.py, deadlines.py, http.py, src/handlers, src/requirements.txt, scripts, tests nuevos, docs, portales/index.html, portales/config.example.json, requirements-task3.txt, samconfig.toml y .gitignore. Revisar git diff --cached; no añadir config.json ni archivos del ZIP.
- Commit, push a fabian/base, PR revisado hacia main. Integrar código exacto que se probó.

## Verificación de preparación

Ejecutado aquí: compilación Python, 10 tests unitarios de autorización/plazos, 4 comprobaciones JS de reglas de suscripción en Node y revisión estructural YAML (13 tablas, Cognito Lite, atributos no escribibles, CORS y filtro INSERT). No se dispone de SAM, boto3 ni pytest en el entorno de preparación: sam validate --lint, sam build, despliegue y 15 tests AWS quedan pendientes de CloudShell. No se afirma compatibilidad de despliegue verificada hasta ejecutar esos comandos. Ver docs/datos-tarea3.md para alcance y limitaciones.
