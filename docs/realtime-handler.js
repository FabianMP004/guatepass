import { util } from '@aws-appsync/utils';
export function onSubscribe(ctx) {
  const c = ctx.identity.claims;
  const groups = c['cognito:groups'] || [];
  const toll = c['custom:peaje_id'];
  if (!groups.includes('operador') || !toll || c.token_use !== 'id') {
    util.unauthorized();
  }
  // Exact channel: blocks wildcard and any other toll.
  if (ctx.info.channel.path !== '/peajes/' + toll) {
    util.unauthorized();
  }
}
export function onPublish(ctx) { return ctx.events; }
