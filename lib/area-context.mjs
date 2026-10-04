import {Providers,fetchJSON} from './providers.mjs';
import {bboxGeometry,segmentIntersectsGeometry} from './geo.mjs';

export const AREA_SOURCES={
  streams:{name:'NCEM · named stream centerlines',url:'https://spartagis.ncem.org/arcgis/rest/services/Public/FRIS_FloodZones/MapServer/0',fields:'*',limit:200,where:'1=1'},
  flood:{name:'NC OneMap · NCEM flood zones',url:'https://spartagis.ncem.org/arcgis/rest/services/Public/FRIS_FloodZones/MapServer/2',fields:'ZONE_LID_VALUE,ZONESUB_LID_VALUE,SFHA_TF',limit:200,where:'SFHA_TF=1'},
  tracts:{name:'Census TIGERweb · tract boundaries',url:'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/0',fields:'GEOID,NAME',limit:100,where:"STATE='37'"},
  epa:{name:'EPA ECHO · regulated facilities',url:'https://echogeo.epa.gov/arcgis/rest/services/ECHO/Facilities/MapServer/0',fields:'REGISTRY_ID,FAC_NAME,FAC_CITY,FAC_ACTIVE_FLAG',limit:100,where:'1=1'}
};
const text=value=>String(value??'').slice(0,300);
export function safePublicUrl(value){try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:null;}catch{return null;}}
export function normalizeAreaFeatures(kind,data){
  if(kind==='streams'&&!data.error&&Array.isArray(data.features)&&data.type!=='FeatureCollection')data={...data,type:'FeatureCollection',features:data.features.map(f=>({properties:f.attributes,geometry:f.geometry?.paths?{type:'MultiLineString',coordinates:f.geometry.paths}:null}))};
  if(data.error||data.type!=='FeatureCollection'||!Array.isArray(data.features))throw new Error('The map service did not return usable features.');
  const config=AREA_SOURCES[kind];
  const features=data.features.slice(0,config.limit).filter(f=>f.geometry&&['Point','Polygon','MultiPolygon','LineString','MultiLineString'].includes(f.geometry.type)).map(f=>{
    const p=f.properties||{};
    const field=suffix=>Object.entries(p).find(([key])=>key===suffix||key.endsWith('.'+suffix))?.[1];
    const properties=kind==='streams'?{name:text(field('WTR_NM')||field('SEGNAME'))}:kind==='flood'?{zone:text(p.ZONE_LID_VALUE),subtype:text(p.ZONESUB_LID_VALUE),specialFloodHazard:Number(p.SFHA_TF)===1}:kind==='tracts'?{geoid:text(p.GEOID),name:text(p.NAME)}:{id:text(p.REGISTRY_ID),name:text(p.FAC_NAME),city:text(p.FAC_CITY),active:p.FAC_ACTIVE_FLAG==='Y'};
    return {type:'Feature',geometry:f.geometry,properties};
  });
  return {type:'FeatureCollection',features,partial:!!data.exceededTransferLimit||data.features.length>=config.limit};
}
export class AreaContext {
  constructor(store,{fetchImpl=fetchJSON,apiKey=process.env.DATA_GOV_API_KEY}={}){this.store=store;this.providers=new Providers(store);this.fetch=fetchImpl;this.apiKey=apiKey;}
  async layer(kind,region){
    const c=AREA_SOURCES[kind];
    return this.providers.cached(`area-${kind}-${region.id}`,c.name,86400000,async()=>{
      const q=new URLSearchParams({f:kind==='streams'?'json':'geojson',where:c.where,geometry:region.bbox.join(','),geometryType:'esriGeometryEnvelope',inSR:'4326',outSR:'4326',spatialRel:'esriSpatialRelIntersects',outFields:c.fields,returnGeometry:'true',geometryPrecision:'5',maxAllowableOffset:'0.00005',resultRecordCount:String(c.limit)});
      if(kind==='streams')q.delete('resultRecordCount'); // This joined layer does not support pagination.
      try{return normalizeAreaFeatures(kind,await this.fetch(`${c.url}/query?${q}`,{timeout:12000}));}catch{throw new Error('This map source is unavailable. Try again later.');}
    });
  }
  async catalog(region){
    if(!this.apiKey)return {data:null,source:{id:`area-catalog-${region.id}`,name:'Data.gov · dataset discovery',status:'needs-key',detail:'Dataset discovery needs a Data.gov API key.'}};
    return this.providers.cached(`area-catalog-${region.id}`,'Data.gov · dataset discovery',86400000,async()=>{
      const q=new URLSearchParams({q:'flood',per_page:'5',spatial_geometry:JSON.stringify(bboxGeometry(region.bbox)),spatial_within:'false'});
      try{
        const data=await this.fetch(`https://api.gsa.gov/technology/datagov/v4/search?${q}`,{timeout:12000,headers:{'X-Api-Key':this.apiKey}});
        if(!Array.isArray(data.results))throw Error();
        return data.results.slice(0,5).map(d=>({title:text(d.title),publisher:text(typeof d.publisher==='string'?d.publisher:d.dcat?.publisher?.name),modified:text(d.dcat?.modified),url:safePublicUrl(d.landingPage||d.dcat?.landingPage)||(/^[a-zA-Z0-9-]+$/.test(d.slug||'')?`https://catalog.data.gov/dataset/${d.slug}`:null)}));
      }catch{throw new Error('Dataset discovery is unavailable. Check the Data.gov key or try later.');}
    });
  }
  async load(region){
    const [flood,tracts,epa,catalog,streams]=await Promise.all([this.layer('flood',region),this.layer('tracts',region),this.layer('epa',region),this.catalog(region),this.layer('streams',region)]);
    const result={region:region.id,checkedAt:new Date().toISOString(),flood,tracts,epa,catalog,streams};this.store.set(`area-context-${region.id}`,result);return result;
  }
  peek(region){return this.store.get(`area-context-${region.id}`)?.data||null;}
  forChat(region,{question,enabled}){return question&&enabled?this.load(region):Promise.resolve(this.peek(region));}
}
export function floodWaterways(context){
  const floods=(context.flood?.data?.features||[]).filter(f=>['Polygon','MultiPolygon'].includes(f.geometry?.type));
  const bounds=g=>{const points=g.coordinates.flat(g.type==='MultiPolygon'?2:g.type==='Polygon'?1:0);return [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];};
  const polygons=floods.map(f=>({geometry:f.geometry,bbox:bounds(f.geometry)}));
  const names=new Set();
  for(const stream of context.streams?.data?.features||[]){
    const name=stream.properties.name;if(!name||/^(unnamed|unknown|not available)$/i.test(name))continue;
    const lines=stream.geometry?.type==='LineString'?[stream.geometry.coordinates]:stream.geometry?.type==='MultiLineString'?stream.geometry.coordinates:[];
    if(lines.some(line=>polygons.some(p=>{
      const b=bounds({type:'LineString',coordinates:line});if(b[0]>p.bbox[2]||b[2]<p.bbox[0]||b[1]>p.bbox[3]||b[3]<p.bbox[1])return false;
      return line.some((point,i)=>i>0&&segmentIntersectsGeometry(line[i-1],point,p.geometry));
    })))names.add(name);
  }
  return [...names].sort().slice(0,20);
}
export function areaContextEvidence(context){
  if(!context)return {status:'not-loaded',detail:'No area-context result is available for this city.'};
  return {region:context.region,checkedAt:context.checkedAt,referenceOnly:true,sources:['flood','tracts','epa','catalog',...(context.streams?['streams']:[])].map(kind=>({kind,...context[kind].source,returnedFeatures:context[kind].data?.features?.length??null,partial:context[kind].data?.partial??false})),floodZones:context.flood.data?{returnedFeatures:context.flood.data.features.length,partial:context.flood.data.partial||!!context.streams?.data?.partial,zones:[...new Set(context.flood.data.features.map(f=>f.properties.zone))],waterwaysIntersectingMappedZones:floodWaterways(context),locationMethod:'Named NCEM stream centerlines intersect the returned reference flood polygons. This is a partial set of mapped waterway corridors, not a list of affected neighborhoods.',mapUrl:'https://fris.nc.gov/map'}:null,tracts:context.tracts.data?.features.slice(0,15).map(f=>f.properties)||[],facilities:context.epa.data?.features.slice(0,20).map(f=>f.properties)||[],datasets:context.catalog.data||[]};
}
