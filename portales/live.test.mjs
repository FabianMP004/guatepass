import test from 'node:test';import assert from 'node:assert/strict';
import {channelFor,mergePass,decodeMessages} from './live.mjs';
test('canal consistente con los peajes originales',()=>{assert.equal(channelFor('IT-123'),'/peajes/p-IT-0123');assert.equal(channelFor('PEAJE_ZONA10'),'/peajes/p-PEAJE-1ZONA10');assert.throws(()=>channelFor('*'))});
test('descarta versiones antiguas y pendientes tras cobro',()=>{const paid={version:2,estado:'COBRADO'};assert.equal(mergePass(paid,{version:1,estado:'PENDIENTE'}),paid);assert.equal(mergePass(paid,{version:3,estado:'PENDIENTE'}),paid);assert.equal(mergePass({version:1,estado:'PENDIENTE'},{version:2,estado:'COBRADO'}).estado,'COBRADO')});
test('decodifica lote AppSync',()=>assert.equal(decodeMessages({type:'data',event:['{"paso_id":"a"}']})[0].paso_id,'a'));
