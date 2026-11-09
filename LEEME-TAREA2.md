# Tarea 2 — instrucciones

Paquete v1.0.0 propuesto, preparado para integrar en el repositorio existente. No sustituye template.yaml ni samconfig.toml. No crea recursos AWS.

1. Descargar y extraer fuera del repo. Comparar y copiar docs/api.md, docs/eventos.md, src/common/models.py y tests/unit/test_contracts.py a la raíz del repo conservando carpetas. Si ya existen, revisar diferencias antes de reemplazar.
2. En CloudShell, dentro del repo: git switch fabian/base (si esa rama ya existe).
3. python3.12 -m venv .venv ; activar con source .venv/bin/activate.
4. Añadir pytest==8.3.5 a requirements-dev.txt si aún no está fijado; resolver cualquier versión existente con el equipo. El archivo requirements-contracts.txt permite instalar solamente estas pruebas sin reemplazar dependencias del proyecto.
5. python -m pip install -r requirements-contracts.txt
6. python -m pytest tests/unit/test_contracts.py -q
7. Cynthia y Tono revisan contratos y ejemplos; documentar aceptación. Integrar mediante revisión de otro integrante.
8. git add docs/api.md docs/eventos.md src/common/models.py tests/unit/test_contracts.py requirements-dev.txt
9. git commit -m "Define contratos API y eventos v1"
10. git push origin fabian/base
11. Tras integrar y verificar el commit aprobado: git tag contracts-v1.0.0 ; git push origin contracts-v1.0.0

No ejecutar los comandos de commit con archivos no creados; ajustar lista si se conserva requirements-contracts.txt en lugar de requirements-dev.txt.

## Verificación realizada

Python 3.12. Se escribieron pruebas primero y se verificó que fallaban con funciones sin implementar (NotImplementedError). Tras implementación, 15 pruebas pasan con python -m unittest discover -s tests/unit -v. Pytest no está instalado en el entorno de preparación y su instalación fue bloqueada por disponibilidad de paquetes; no se afirma haber ejecutado pytest aquí. Las pruebas usan unittest.TestCase, compatible con colección de pytest. Ejecutar el comando exigido arriba en CloudShell antes de dar tarea por cerrada.

## Límites

No hay endpoints desplegados, validación JWT, almacenamiento idempotente, transacciones ni transportes AWS. models.py valida sobre de evento, montos, IDs y transiciones; los schemas detallados por endpoint y payload se especifican en docs y deben implementarse en handlers posteriores. replay compara un registro ya persistido, no almacena respuestas ni protege concurrencia por sí solo.

Los ejemplos de placa, NIT, tag, tarifa y peaje son ilustrativos y deben sustituirse por las semillas reales. Carnés se parametrizan; no se inventan carnés de integrantes.
