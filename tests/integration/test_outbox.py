"""Local integration with Moto plus injected failures. No real AWS writes."""
import json
from unittest.mock import patch, Mock
import boto3
import pytest
from moto import mock_aws
from common.models import make_event
from common.events import outbox_item
from eventos import publicar as publisher
from eventos.recuperar_outbox import recuperar_outbox

def event(i):
    return make_event('PasoRegistrado',f'paso_{i}',1,{'paso_id':f'paso_{i}'},
                      'req','2026-11-09T12:00:00Z','20230223')

@pytest.fixture
def outbox(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
    monkeypatch.setenv('OUTBOX_TABLE','outbox')
    monkeypatch.setenv('EVENT_BUS_NAME','test-bus')
    with mock_aws():
        table = boto3.resource('dynamodb').create_table(TableName='outbox',BillingMode='PAY_PER_REQUEST',
            KeySchema=[{'AttributeName':'evento_id','KeyType':'HASH'}],
            AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ['evento_id','pendiente_shard','creado_en']],
            GlobalSecondaryIndexes=[{'IndexName':'PorPendiente',
                'KeySchema':[{'AttributeName':'pendiente_shard','KeyType':'HASH'},{'AttributeName':'creado_en','KeyType':'RANGE'}],
                'Projection':{'ProjectionType':'ALL'}}])
        boto3.client('events').create_event_bus(Name='test-bus')
        yield table

def test_failure_then_recovery(outbox):
    row = outbox_item(event(1)); outbox.put_item(Item=row)
    with patch.object(publisher,'publicar',return_value=[row['evento_id']]):
        assert recuperar_outbox()['fallidos'] == 1
    assert outbox.get_item(Key={'evento_id':row['evento_id']})['Item']['enviado'] is False
    assert recuperar_outbox()['fallidos'] == 0
    assert outbox.get_item(Key={'evento_id':row['evento_id']})['Item']['enviado'] is True
    assert recuperar_outbox()['revisados'] == 0

def test_accepted_then_mark_failure_republishes_same_id(outbox):
    row = outbox_item(event(1)); outbox.put_item(Item=row)
    seen = []
    def accepted(events):
        seen.extend(e['detail']['evento_id'] for e in events)
        return []
    resource = Mock(); resource.Table.return_value = outbox
    with patch.object(publisher.boto3,'resource',return_value=resource), patch.object(publisher,'publicar',side_effect=accepted):
        with patch.object(outbox,'update_item',side_effect=RuntimeError('mark down')):
            assert publisher.publish_ids([row['evento_id']]) == [row['evento_id']]
        assert publisher.publish_ids([row['evento_id']]) == []
    assert seen == [row['evento_id'],row['evento_id']]
    assert outbox.scan()['Count'] == 1
    # This proves stable duplicate delivery, NOT the future billing consumer.

def test_putevents_partial_and_batch_limit(outbox):
    events = [event(i) for i in range(11)]
    bus = Mock()
    bus.put_events.side_effect = [
        {'FailedEntryCount':1,'Entries':[{'EventId':'ok'}]*9+[{'ErrorCode':'ThrottlingException'}]},
        {'FailedEntryCount':0,'Entries':[{'EventId':'ok'}]}]
    with patch.object(publisher.boto3,'client',return_value=bus):
        assert publisher.publicar(events) == [events[9]['detail']['evento_id']]
    assert [len(c.kwargs['Entries']) for c in bus.put_events.call_args_list] == [10,1]

@pytest.mark.parametrize('result', [
    {'FailedEntryCount':0,'Entries':[{}]},
    {'FailedEntryCount':0,'Entries':[{'ErrorCode':'InternalFailure'}]},
    {'FailedEntryCount':1,'Entries':[{'EventId':'unexpected'}]},
    {'FailedEntryCount':0,'Entries':[]}])
def test_malformed_result_is_not_acknowledged(outbox,result):
    bus = Mock(); bus.put_events.return_value = result
    with patch.object(publisher.boto3,'client',return_value=bus):
        assert publisher.publicar([event(1)]) == [event(1)['detail']['evento_id']]

def test_recovery_paginates(outbox):
    for i in range(105):
        row = outbox_item(event(i)); row['pendiente_shard']='PENDING#00'
        outbox.put_item(Item=row)
    result = recuperar_outbox()
    assert result == {'revisados':105,'fallidos':0,'incompleto':False}

def test_stream_partial_sequence_and_filter():
    record = {'eventName':'INSERT','dynamodb':{'SequenceNumber':'42','Keys':{'evento_id':{'S':'e1'}}}}
    with patch.object(publisher,'publish_ids',return_value=['e1']):
        assert publisher.handler({'Records':[record]},None) == {'batchItemFailures':[{'itemIdentifier':'42'}]}
    with patch.object(publisher,'publish_ids',return_value=[]) as publish:
        publisher.handler({'Records':[dict(record,eventName='MODIFY')]},None)
        publish.assert_called_once_with([])
