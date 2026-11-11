import json
import pytest
from eventos.tiempo_real import channel_for, projection
from common.models import make_event

def test_channel_encoding_preserves_original_toll_and_rejects_wildcard():
    assert channel_for('PEAJE_ZONA10')=='/peajes/p-PEAJE-1ZONA10'
    assert channel_for('IT-PEAJE')=='/peajes/p-IT-0PEAJE'
    with pytest.raises(ValueError):channel_for('*')

def test_projection_has_persisted_version_and_identity():
    event=make_event('paso.pendiente','p1',3,{'paso_id':'p1','placa':'P-123ABC',
        'peaje_id':'PEAJE_ZONA10','monto_centavos':450,'vence_en':'2026-11-10T13:00:00Z'},
        'req','2026-11-10T12:00:00Z','20230223')
    snapshot=projection(event)
    assert snapshot['estado']=='PENDIENTE' and snapshot['version']==3
    assert snapshot['monto_centavos']==450 and snapshot['fecha_local']=='2026-11-10'

def test_template_browser_cannot_publish_and_owns_exact_toll():
    import yaml
    from pathlib import Path
    template=yaml.safe_load(Path('template.yaml').read_text())
    api=template['Resources']['RealtimeApi']['Properties']['EventConfig']
    namespace=template['Resources']['PeajesNamespace']['Properties']
    code=namespace['CodeHandlers']
    assert api['DefaultPublishAuthModes']==[{'AuthType':'AWS_IAM'}]
    assert namespace['PublishAuthModes']==[{'AuthType':'AWS_IAM'}]
    assert namespace['SubscribeAuthModes']==[{'AuthType':'AMAZON_COGNITO_USER_POOLS'}]
    assert "claims['custom:peaje_id']" in code and "groups.includes('operador')" in code
    assert "ctx.info.channel.path !== '/peajes/' + suffix" in code
    assert 'EventPublish' in str(template['Resources']['TiempoRealRole'])
