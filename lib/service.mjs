import {REGIONS,DISCLAIMER} from './config.mjs';
import {Providers} from './providers.mjs';
import {demoSnapshot,demoWithOSM} from './demo.mjs';
import {actionsFor,exposure} from './model.mjs';
export class DisasterService {
  constructor(store,sinks,options={}){this.store=store;this.providers=new Providers(store);this.sinks=sinks;this.demoRoads=options.demoRoads??process.env.DEMO_ROADS??'real';this.demoCache=new Map();this.demoInflight=new Map();this.inflight=new Map();this.listeners=new Set();this.activeRegions=new Set(['raleigh']);}
  decorate(snapshot,includeRoads=false){
    const {roads,...small}=snapshot,data=structuredClone(includeRoads?snapshot:small),now=Date.now();
    data.incidents=data.incidents.filter(i=>!i.expires||Date.parse(i.expires)>now);
    data.facilities=data.facilities.map(f=>exposure(f,data.incidents));
    data.reports=this.store.reports(data.region.id,data.mode);data.actions=actionsFor(data);data.disclaimer=DISCLAIMER;
    data.roadInfo={nodes:roads?.nodes?.length||0,segments:roads?.edges?.length||0,fetchedAt:roads?.fetchedAt||null,simulation:!!roads?.simulation,basis:roads?.basis||(roads?.simulation?'synthetic':'osm')};
    data.stats={alerts:data.incidents.length,resources:data.facilities.length,exposed:data.facilities.filter(f=>f.incidentIds.length).length,reports:data.reports.length};
    if(!includeRoads)delete data.roads;return data;
  }
  async snapshot(regionId,mode='live',includeRoads=false){
    if(mode==='demo'){
      // Cache the prepared graph briefly, including an explicitly marked fallback.
      // This avoids repeated Overpass timeouts and rebuilding 100k+ edges per click.
      let cached=this.demoCache.get(regionId);
      if(!cached||Date.now()-cached.loadedAt>60000){
        if(!this.demoInflight.has(regionId))this.demoInflight.set(regionId,(async()=>{
          const scenario=demoSnapshot(regionId),snapshot=this.demoRoads==='synthetic'?scenario:demoWithOSM(scenario,await this.providers.osm(REGIONS[regionId]));
          const entry={snapshot,loadedAt:Date.now()};this.demoCache.set(regionId,entry);return entry;
        })().finally(()=>this.demoInflight.delete(regionId)));
        cached=await this.demoInflight.get(regionId);
      }
      return this.decorate(cached.snapshot,includeRoads);
    }
    this.activeRegions.add(regionId);
    let existing=this.store.get(`snapshot-${regionId}`);
    if(!existing||Date.now()-Date.parse(existing.updated)>60000)await this.refresh(regionId);
    existing=this.store.get(`snapshot-${regionId}`);
    return this.decorate(existing.data,includeRoads);
  }
  refresh(regionId){
    if(this.inflight.has(regionId))return this.inflight.get(regionId);
    const job=(async()=>{
      const region=REGIONS[regionId];
      const results=await Promise.all([this.providers.alerts(region),this.providers.weather(region),this.providers.sensor(region),this.providers.osm(region),this.providers.census(region)]);
      const [alerts,weather,sensor,osm,census]=results;
      const snapshot={region,mode:'live',fetchedAt:new Date().toISOString(),incidents:alerts.data||[],weather:weather.data,sensor:sensor.data,facilities:osm.data?.facilities||[],roads:osm.data?.roads||null,demographics:census.data,sources:results.map(r=>r.source)};
      this.store.set(`snapshot-${regionId}`,snapshot);
      for(const listener of this.listeners)listener(regionId,this.decorate(snapshot));
      // Optional sinks never delay the core map or sensor stream.
      if(this.sinks)this.sinks.deliver(region.gauge).catch(()=>{});
      return snapshot;
    })().finally(()=>this.inflight.delete(regionId));this.inflight.set(regionId,job);return job;
  }
}
