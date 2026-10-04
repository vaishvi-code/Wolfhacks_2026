export async function requestRoute({body,snapshot,offline,online,api,loadPack,planEvacuation}) {
  if(!offline&&online) {
    try{return {plan:await api('/api/evacuate',body),offline:false};}
    catch(error){
      // HTTP validation/provider errors must remain visible. Only connectivity
      // failures fall back to a saved, independently checked snapshot.
      if(!(error instanceof TypeError)&&!['TimeoutError','AbortError'].includes(error.name))throw error;
    }
  }
  const pack=offline&&snapshot?.roads?.nodes?.length?snapshot:await loadPack(body.region,body.mode);
  if(!pack)throw new Error(`No offline pack saved for ${body.region} (${body.mode}). Reconnect and select Save offline for this city and mode.`);
  return {plan:planEvacuation(pack,body,{offline:true,requireMappedRoads:true}),snapshot:pack,offline:true};
}
