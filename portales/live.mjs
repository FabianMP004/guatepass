export function channelFor(toll){
  if(!/^[A-Za-z0-9_-]{1,22}$/.test(toll)) throw Error('Peaje inválido');
  const suffix='p-'+toll.replaceAll('-', '-0').replaceAll('_', '-1');
  return '/peajes/'+suffix;
}
export function mergePass(current,incoming){
  if(!current) return incoming;
  const old=Number(current.version??0), next=Number(incoming.version??0);
  if(!Number.isInteger(next)||next<old) return current;
  if(current.estado==='COBRADO' && incoming.estado==='PENDIENTE')return current;
  if(next===old && current.estado!==incoming.estado)return current;
  return {...current,...incoming};
}
export function decodeMessages(frame){
  if(frame.type!=='data')return [];
  return (frame.event||[]).map(value=>typeof value==='string'?JSON.parse(value):value);
}
