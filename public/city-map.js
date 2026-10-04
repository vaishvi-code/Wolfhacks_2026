import {loadCityMap} from './offline.js';
import {BlobMapSource} from './map-download.js';

export function createCityBasemap(map,{onStatus=()=>{},onFallback=()=>{}}={}){
  let layer=null,key='',revision=0,forceRevision=0;
  const remove=()=>{if(layer){map.removeLayer(layer);layer=null;}};
  const attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · <a href="https://protomaps.com">Protomaps</a>';
  return {
    refresh(){key='';forceRevision++;},
    async update(region,offline){
      const next=`${region}:${offline}:${forceRevision}`;
      if(key===next)return;key=next;const current=++revision;remove();map.setMaxBounds(null);
      onStatus('Loading city map…');
      try{
        const saved=await loadCityMap(region);
        let source,entry;
        if(saved?.blob){entry=saved;source=new BlobMapSource(saved.blob,`${region}:${saved.sha256}`);}
        else{
          if(offline)throw new Error('No city map downloaded. Reconnect and use Save offline.');
          const response=await fetch('/maps/catalog.json');if(!response.ok)throw new Error('City map catalog is unavailable.');
          entry=(await response.json()).regions[region];if(!entry)throw new Error('City map is unavailable.');
          source=new pmtiles.FetchSource(entry.url);
        }
        const archive=new pmtiles.PMTiles(source);
        await archive.getHeader();
        if(current!==revision)return;
        const [w,s,e,n]=entry.bbox;
        layer=protomapsL.leafletLayer({url:archive,flavor:'light',lang:'en',maxDataZoom:entry.maxDataZoom,levelDiff:1,maxZoom:19,noWrap:true,bounds:[[s,w],[n,e]],attribution});
        layer.addTo(map);
        // Keep panning inside the packaged city; routing has its own smaller coverage.
        map.setMaxBounds([[s,w],[n,e]]);
        onStatus(saved?.blob?'Downloaded city map · zoom to street detail':'City map · Save offline to download');
        onFallback(false);
      }catch(error){
        if(current!==revision)return;
        remove();onStatus(error.message);onFallback(true);
      }
    }
  };
}
