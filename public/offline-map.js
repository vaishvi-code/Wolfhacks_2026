// Use the downloaded routing graph as a vector basemap; no tile requests.
export function roadFeatures(graph) {
  const nodes=new Map((graph?.nodes||[]).map(node=>[node.id,node.coordinates]));
  const features=[];
  for(const edge of graph?.edges||[]) {
    const from=nodes.get(edge.from),to=nodes.get(edge.to);
    if(!from||!to)continue;
    features.push({type:'Feature',geometry:{type:'LineString',coordinates:[from,to]},properties:{name:edge.name||'Unnamed road'}});
  }
  return {type:'FeatureCollection',features};
}
