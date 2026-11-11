# Tarea 10 — portal y tiempo real

Aplicar sobre **main fusionado tras tarea 9** en la rama de trabajo propia. No reemplazar credenciales, CSV ni parámetros del stack. `template.yaml` reutiliza AppSync y CloudFront existentes; añade historial, tiempo real y rutas de lectura. El frontend jamás recibe la clave del webhook.

```bash
cd ~/proyectos/guatepass
git switch fabian/base
git fetch origin
git merge origin/main
git status --short
mkdir -p "$HOME/guatepass-tarea10-parche"
unzip -oq "$HOME/guatepass-tarea10.zip" -d "$HOME/guatepass-tarea10-parche"
python3.12 "$HOME/guatepass-tarea10-parche/instalar_parche_tarea10.py" --repo "$PWD"
python3.12 "$HOME/guatepass-tarea10-parche/instalar_parche_tarea10.py" --repo "$PWD" --apply
source .venv-task5/bin/activate
.venv-task5/bin/python -m pip install -r requirements-task10.txt
.venv-task5/bin/python -m pytest tests/integration/test_portal_auth.py tests/integration/test_realtime.py -v
.venv-task5/bin/python -m pytest -q
node --test portales/live.test.mjs
sam validate --lint --template-file template.yaml --region us-east-1
sam build --template-file template.yaml
```

Desplegar con los parámetros reales existentes, como en las tareas 7–9; no cambiar OwnerCarnet ni timeouts. La primera comprobación requiere `UPDATE_COMPLETE` y `aws lambda list-event-source-mappings --function-name guatepass-dev-tiempo-real --region us-east-1`. Si el despliegue falla, inspeccionar `aws cloudformation describe-stack-events --stack-name guatepass-dev --region us-east-1 --max-items 20` antes de reintentar.

```bash
.venv-task5/bin/python scripts/export_config.py --stack guatepass-dev --region us-east-1
.venv-task5/bin/python scripts/publish_portals.py --stack guatepass-dev --region us-east-1
```

La publicación devuelve URL e invalidación CloudFront. Esperar a que finalice la invalidación antes de abrir el sitio. Los archivos publicados son index.html, app.js, live.mjs, styles.css y config.json. Abrir dos navegadores independientes para conductor y operador; probar login/logout, recarga de página, paginación, saldo, TAG, historial y suscripción. La ruta del canal usa la transformación reversible `p-` + guiones `-0` + guiones bajos `-1`: `PEAJE_ZONA10` → `/peajes/p-PEAJE-1ZONA10`. El handler AppSync compara exclusivamente el peaje del token; no admite rutas de otro peaje ni comodines.

## CP2 con datos reales

1. Comprobar que Juan (P-123ABC) aún no tiene TAG y que Andrés (P-666YZA) tiene saldo Q0, TAG-003 y no tiene cobros previos de esta prueba. Si ya hubo movimientos, documentar el saldo anterior y usar un ambiente de demo limpio para el saldo final exigido.
2. Juan inicia sesión en CloudFront, asocia TAG-005; operador inicia sesión en otro navegador y confirma conexión «En vivo» para Zona 10. Enviar aviso: `.venv-task5/bin/python scripts/probar_cp2_tarea10.py --placa P-123ABC`. Confirmar `COBRADO` Q4.50 en portal y movimientos.
3. Andrés inicia sesión, verifica TAG-003 y saldo cero. Enviar `.venv-task5/bin/python scripts/probar_cp2_tarea10.py --placa P-666YZA`; guardar `paso_id` y confirmar `PENDIENTE` Q4.50 con `vence_en` y ejecución RUNNING de Step Functions.
4. Andrés recarga Q20. Confirmar `COBRADO` Q4.50, saldo Q15.50, un solo movimiento de cobro, ejecución SUCCEEDED y vista del operador actualizada. **Si el plazo demo de 120 segundos vence, repetir únicamente en un entorno de demo recreado; nunca reescribir saldos manualmente.**
5. Probar suscripción como operador de otro peaje y como conductor: ambas deben ser rechazadas; volver a cargar la pestaña y comprobar que reaparecen sesión e historial. Registrar IDs de paso, estados y tiempos en VERIFICACION-TAREA10.md.

No ejecutar una prueba de cámara con TAG si el vínculo correspondiente no está listo. Para reintentar recarga desde el navegador, conservar el mismo monto y la misma clave hasta que se confirme la respuesta.
