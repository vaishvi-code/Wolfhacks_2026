import test from 'node:test';
import assert from 'node:assert/strict';
import {requestRoute} from '../public/route-request.js';
const body={region:'raleigh',mode:'demo'},pack={roads:{nodes:[{}]}};
test('connection loss falls back even when browser still reports online',async()=>{
  const result=await requestRoute({body,online:true,api:async()=>{throw new TypeError('Failed to fetch');},loadPack:async(region,mode)=>{assert.equal(region,'raleigh');assert.equal(mode,'demo');return pack;},planEvacuation:(snapshot,request,options)=>{assert.equal(snapshot,pack);assert.equal(options.offline,true);return {status:'ok'};}});
  assert.equal(result.offline,true);assert.equal(result.plan.status,'ok');
});
test('offline route never calls server and explains missing pack',async()=>{
  await assert.rejects(requestRoute({body,online:false,api:()=>assert.fail('network called'),loadPack:async()=>null}),/No offline pack saved/);
});
test('a timed-out route request uses the saved pack',async()=>{
  const result=await requestRoute({body,online:true,api:async()=>{throw new DOMException('Timed out','TimeoutError');},loadPack:async()=>pack,planEvacuation:()=>({status:'stale_data'})});
  assert.equal(result.offline,true);
  assert.equal(result.plan.status,'stale_data');
});
test('server validation errors are not hidden by offline fallback',async()=>{
  await assert.rejects(requestRoute({body,online:true,api:async()=>{throw new Error('Invalid origin');},loadPack:()=>assert.fail('fallback called')}),/Invalid origin/);
});
