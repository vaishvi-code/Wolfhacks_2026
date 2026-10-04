import test from 'node:test';
import assert from 'node:assert/strict';
import {riverTrend} from '../lib/conditions.mjs';
import {affectingEvents} from '../lib/route-events.mjs';
import {StreamSinks} from '../lib/integrations.mjs';
import {Store} from '../lib/store.mjs';
const now=Date.now(),stamp=n=>new Date(now+n*60000).toISOString();
test('river trends require fresh observations and real history, and preserve negative gage datums',()=>{
  const readings=[{time:stamp(-65),value:-2},{time:stamp(-5),value:-1.2}];
  const trend=riverTrend(readings,now);assert.equal(trend.status,'available');assert.ok(Math.abs(trend.changeFt-.8)<1e-9);assert.equal(trend.windowMinutes,60);
  assert.equal(riverTrend(readings,now+3600000).status,'stale');assert.equal(riverTrend(readings.slice(1),now).status,'insufficient_history');
  assert.equal(riverTrend([{time:'bad',value:1},{time:stamp(5),value:2},{time:stamp(-5),value:null}],now).status,'unavailable');
});
test('only active road events near the actual route trigger a route change',()=>{
  const route={geometry:{type:'LineString',coordinates:[[0,0],[.01,0]]}},event={id:'e',kind:'blocked_road',coordinates:[.005,0],expires:stamp(10),status:'unverified'};
  assert.equal(affectingEvents(route,[event],now).length,1);
  for(const patch of [{expires:stamp(-1)},{coordinates:[.005,.01]},{kind:'heat_concern'},{status:'resolved'}])assert.equal(affectingEvents(route,[{...event,...patch}],now).length,0);
});
test('Tiger event delivery is retryable, parameterized and isolated by city and mode',async()=>{
  const store=new Store(':memory:');try{
    const event={id:'test-event',region:'raleigh',mode:'demo',kind:'blocked_road',coordinates:[-78.63,35.77],description:'Simulated test closure',status:'unverified',simulation:true,createdAt:stamp(0),expires:stamp(10)};
    store.addReport(event);const sink=new StreamSinks(store);let writes=0;
    sink.pool={query:async(sql,values)=>{if(sql.startsWith('INSERT')){writes++;assert.equal(values[2],'raleigh');assert.equal(values[3],'demo');assert.equal(values[10],true);return {rows:[]};}assert.deepEqual(values,['raleigh','demo']);return {rows:[{event_id:event.id,occurred_at:event.createdAt,kind:event.kind,longitude:event.coordinates[0],latitude:event.coordinates[1],description:event.description,status:event.status,expires_at:event.expires,simulation:true}]};}};
    const result=await sink.conditions({id:'raleigh',gauge:'test'},'demo');
    assert.equal(result.source,'tiger');assert.equal(result.events.length,1);assert.equal(result.trend,null);assert.equal(writes,1);
    await sink.deliverEvents();assert.equal(writes,1);assert.equal(store.roadEvents('raleigh','live').length,0);
    sink.pool.query=async()=>{throw Error('private provider error');};
    const fallback=await sink.conditions({id:'raleigh'},'demo');assert.equal(fallback.source,'local');assert.equal(fallback.events.length,1);assert.ok(!JSON.stringify(fallback).includes('private provider error'));
  }finally{store.close();}
});
test('Tiger live queries return raw trends and continuous aggregate summaries',async()=>{
  const store=new Store(':memory:');try{
    const sink=new StreamSinks(store),queries=[];
    sink.pool={query:async(sql,values)=>{queries.push(sql);if(sql.includes('FROM road_events')){assert.deepEqual(values,['raleigh','live']);return {rows:[]};}assert.deepEqual(values,['gage']);return {rows:sql.includes('FROM sensor_hourly')?[{bucket:stamp(-60),average_ft:3,observations:4}]:[{observed_at:stamp(-65),gage_height_ft:2},{observed_at:stamp(-5),gage_height_ft:3}]};}};
    const result=await sink.conditions({id:'raleigh',gauge:'gage'},'live');assert.equal(result.trend.changeFt,1);assert.equal(result.hourly.length,1);assert.ok(queries.some(q=>q.includes('sensor_hourly')));
  }finally{store.close();}
});
