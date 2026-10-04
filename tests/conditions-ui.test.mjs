import test from 'node:test';
import assert from 'node:assert/strict';
import {initConditions} from '../public/conditions.js';

test('adding a closure from saved conditions saves the selected destination and automatically replans',async t=>{
  const handlers={},panel={hidden:false,innerHTML:'',querySelectorAll:()=>[],querySelector:id=>({addEventListener:(_,fn)=>{handlers[id]=fn;}})};
  const previous=globalThis.document;globalThis.document={getElementById:()=>panel};
  t.after(()=>{globalThis.document=previous;});
  const route={destination:{id:'selected-alternative'}},state={mode:'demo',offline:true,epoch:1,origin:[-78.6,35.8],snapshot:{conditions:{events:[]}},plan:{minimumClearanceKm:.4}};
  const calls=[];
  const ui=initConditions({state,isOnline:()=>true,context:()=>({mode:'demo',minimumClearanceKm:1}),selectedRoute:()=>state.plan?route:null,
    api:async(path,body)=>{calls.push('save');assert.equal(path,'/api/demo-closure');assert.equal(body.destinationId,route.destination.id);assert.equal(body.minimumClearanceKm,.4);return {snapshot:{conditions:{events:[]}}};},
    acceptSnapshot:s=>{calls.push('snapshot');state.snapshot=s;state.plan=null;},findRoute:async()=>{assert.equal(state.offline,false);calls.push('replan');},toast:()=>calls.push('toast')});
  ui.render();assert.doesNotMatch(panel.innerHTML,/id="simulate-closure" disabled/);
  await handlers['#simulate-closure']();assert.deepEqual(calls,['save','snapshot','replan','toast']);assert.doesNotMatch(panel.innerHTML,/Updating route/);
});

test('failed closure saves release the button and do not clear or replan the route',async t=>{
  const handlers={},panel={querySelectorAll:()=>[],querySelector:id=>({addEventListener:(_,fn)=>{handlers[id]=fn;}})};
  const previous=globalThis.document;globalThis.document={getElementById:()=>panel};t.after(()=>{globalThis.document=previous;});
  const state={mode:'demo',epoch:1,snapshot:{conditions:{events:[]}},plan:{},origin:[-78.6,35.8]};let message;
  const ui=initConditions({state,isOnline:()=>true,context:()=>({}),selectedRoute:()=>({destination:{id:'dest'}}),api:async()=>{throw Error('Server unavailable');},acceptSnapshot:()=>assert.fail('Must preserve route'),findRoute:()=>assert.fail('Must not replan'),toast:m=>{message=m;}});
  ui.render();await handlers['#simulate-closure']();assert.equal(message,'Server unavailable');assert.doesNotMatch(panel.innerHTML,/id="simulate-closure" disabled/);
});
