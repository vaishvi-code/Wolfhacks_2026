import test from 'node:test';
import assert from 'node:assert/strict';
import {AreaContext,normalizeAreaFeatures,areaContextEvidence,safePublicUrl,floodWaterways} from '../lib/area-context.mjs';
import {Store} from '../lib/store.mjs';
import {REGIONS} from '../lib/config.mjs';
import {areaContextCards} from '../public/area-context.js';

const collection=properties=>({type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Point',coordinates:[-78.6,35.7]},properties}]});
test('flood location evidence names only streams intersecting returned flood polygons',()=>{
  const polygon={type:'Polygon',coordinates:[[[0,0],[2,0],[2,2],[0,2],[0,0]]]};
  const stream=(name,coordinates)=>({geometry:{type:'LineString',coordinates},properties:{name}});
  const context={flood:{data:{features:[{geometry:polygon}]}},streams:{data:{features:[stream('Mapped Creek',[[-1,1],[3,1]]),stream('Distant Creek',[[4,4],[5,5]]),stream('Mapped Creek',[[1,0],[1,2]]),stream('Unnamed',[[1,0],[1,2]])]}}};
  assert.deepEqual(floodWaterways(context),['Mapped Creek']);
  const normalized=normalizeAreaFeatures('streams',{type:'FeatureCollection',features:[{geometry:{type:'LineString',coordinates:[[0,0],[1,1]]},properties:{'NC_FLOOD.DBO.S_WTR_NM.WTR_NM':'Mapped Creek'}}]});
  assert.equal(normalized.features[0].properties.name,'Mapped Creek');
});
test('chat loads area data without a manual Updates visit, including non-English questions',async()=>{
  const store=new Store(':memory:');try{
    let calls=0;
    const area=new AreaContext(store,{apiKey:'',fetchImpl:async()=>{calls++;return collection({});}});
    assert.equal(await area.forChat(REGIONS.raleigh,{question:'What APIs do you use?',enabled:false}),null);assert.equal(calls,0);
    const result=await area.forChat(REGIONS.raleigh,{question:'Quels jeux de données utilisez-vous ?',enabled:true});assert.equal(result.region,'raleigh');assert.equal(calls,4);
    await area.forChat(REGIONS.raleigh,{question:'What does EPA provide?',enabled:true});assert.equal(calls,4);
    await area.forChat(REGIONS.asheville,{question:'What datasets are available?',enabled:true});assert.equal(calls,8);
  }finally{store.close();}
});
test('area sources use bounded city queries, sanitize fields, cache requests and keep secrets out of evidence',async()=>{
  const store=new Store(':memory:');try{
    const calls=[];const area=new AreaContext(store,{apiKey:'PRIVATE-KEY',fetchImpl:async(url,options)=>{
      calls.push(url);const u=new URL(url);
      if(u.host==='api.gsa.gov'){assert.equal(options.headers['X-Api-Key'],'PRIVATE-KEY');assert.equal(u.searchParams.get('per_page'),'5');return {results:[{title:'Flood data',publisher:'NC',slug:'flood-data',landingPage:'javascript:bad',dcat:{modified:'2025-01-01'}}]};}
      assert.equal(u.searchParams.get('geometry'),REGIONS.raleigh.bbox.join(','));assert.equal(u.searchParams.get('outSR'),'4326');assert.ok(Number(u.searchParams.get('resultRecordCount'))<=200);
      if(u.host==='spartagis.ncem.org')return collection({ZONE_LID_VALUE:'AE',SFHA_TF:1,private:'SECRET'});
      if(u.host==='tigerweb.geo.census.gov')return collection({GEOID:'37183000100',NAME:'1',private:'SECRET'});
      return collection({REGISTRY_ID:'123',FAC_NAME:'Facility',FAC_CITY:'Raleigh',FAC_ACTIVE_FLAG:'Y',private:'SECRET'});
    }});
    const result=await area.load(REGIONS.raleigh);assert.equal(calls.length,5);await area.load(REGIONS.raleigh);assert.equal(calls.length,5);
    assert.equal(result.catalog.data[0].url,'https://catalog.data.gov/dataset/flood-data');
    const evidence=areaContextEvidence(area.peek(REGIONS.raleigh));assert.equal(evidence.floodZones.returnedFeatures,1);assert.equal(evidence.facilities[0].name,'Facility');assert.ok(!JSON.stringify(evidence).includes('SECRET'));assert.ok(!JSON.stringify(evidence).includes('PRIVATE-KEY'));assert.ok(!JSON.stringify(evidence).includes('coordinates'));
    assert.equal(area.peek(REGIONS.asheville),null);
  }finally{store.close();}
});
test('unavailable providers and missing Data.gov key never claim zero features or expose provider errors',async()=>{
  const store=new Store(':memory:');try{
    const area=new AreaContext(store,{apiKey:'',fetchImpl:async()=>{throw Error('SECRET');}});
    const result=await area.load(REGIONS.raleigh);assert.equal(result.flood.source.status,'unavailable');assert.equal(result.flood.data,null);assert.equal(result.catalog.source.status,'needs-key');assert.ok(!JSON.stringify(result).includes('SECRET'));
    assert.match(areaContextCards(result),/Unavailable/);assert.ok(!areaContextCards(result).includes('0 mapped features'));
  }finally{store.close();}
});
test('ArcGIS errors are rejected, capped results marked partial, and unsafe links rejected',()=>{
  assert.throws(()=>normalizeAreaFeatures('flood',{error:{message:'private'}}));
  const data=collection({FAC_NAME:'<img src=x>',REGISTRY_ID:'1'});data.features=Array(101).fill(data.features[0]);
  const normalized=normalizeAreaFeatures('epa',data);assert.equal(normalized.features.length,100);assert.equal(normalized.partial,true);
  assert.equal(safePublicUrl('https://user:password@example.com'),null);assert.equal(safePublicUrl('javascript:alert(1)'),null);
});
