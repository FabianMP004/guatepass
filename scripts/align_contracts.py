"""Align known Task 2 conventions with explicit Task 3 instructions."""
from pathlib import Path
p=Path('docs/api.md')
if not p.exists():raise SystemExit('Falta docs/api.md de tarea 2. Integrarla antes de continuar.')
s=p.read_text()
for old,new in [('Bearer <access_token>','<id_token>'),('token_use y client_id autorizado','token_use=id y aud del app client autorizado'),('CONDUCTOR','conductor'),('OPERADOR','operador'),('ATENCION','atencion'),('ADMINISTRADOR','administrador')]:s=s.replace(old,new)
note='\n## Ajuste de autenticación — tarea 3\n\nAPI Gateway REST con autorizador Cognito valida el ID token del app client (aud), sin scopes OAuth en los métodos. Los grupos canónicos son conductor, operador, atencion y administrador. Los atributos custom:placa, custom:peaje_id y custom:carnet se escriben administrativamente y son sólo lectura para el app client. Cada Lambda verifica el rol y la propiedad. La tabla Usuarios se consulta por placa con lectura consistente y también se verifica sub. Esta instrucción reemplaza el uso de access token de la propuesta inicial.\n'
if '## Ajuste de autenticación — tarea 3' not in s:s+=note
p.write_text(s);print('docs/api.md actualizado; revisar git diff antes de integrar.')
