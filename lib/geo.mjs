const RAD = Math.PI / 180;
export function validCoordinate(p) { return Array.isArray(p) && p.length===2 && p.every(Number.isFinite) && Math.abs(p[0])<=180 && Math.abs(p[1])<=90; }
export function distanceKm(a, b) {
  const dlat = (b[1] - a[1]) * RAD, dlon = (b[0] - a[0]) * RAD;
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.sin(dlon / 2) ** 2;
  return 6371.0088 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
}
function onSegment(p, a, b) {
  const cross = (p[1] - a[1]) * (b[0] - a[0]) - (p[0] - a[0]) * (b[1] - a[1]);
  return Math.abs(cross) < 1e-10 && p[0] >= Math.min(a[0], b[0]) && p[0] <= Math.max(a[0], b[0]) && p[1] >= Math.min(a[1], b[1]) && p[1] <= Math.max(a[1], b[1]);
}
export function projectOnSegment(p, a, b) {
  const scale = Math.cos(p[1] * RAD), dx = (b[0] - a[0]) * scale, dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, (((p[0] - a[0]) * scale * dx) + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  const coordinates=[a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
  return {coordinates,t,distance:distanceKm(p,coordinates)};
}
export function segmentDistanceKm(p, a, b) {
  return projectOnSegment(p,a,b).distance;
}
// Local planning distance to the nearest polygon edge, including holes.
export function distanceToGeometryKm(point, geometry) {
  if (!geometry) return NaN;
  if (geometry.type === 'GeometryCollection') return Math.min(...geometry.geometries.map(g=>distanceToGeometryKm(point,g)));
  if (pointInGeometry(point,geometry)) return 0;
  const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:[];
  if (!polygons.length) return NaN;
  let nearest=Infinity;
  for(const poly of polygons)for(const ring of poly)for(let i=0;i<ring.length;i++)nearest=Math.min(nearest,segmentDistanceKm(point,ring[i],ring[(i+1)%ring.length]));
  return nearest;
}
function crosses(a, b, c, d) {
  const orient = (p, q, r) => (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0]);
  const x = orient(a,b,c), y = orient(a,b,d), z = orient(c,d,a), w = orient(c,d,b);
  return (x*y < 0 && z*w < 0) || (Math.abs(x)<1e-12 && onSegment(c,a,b)) || (Math.abs(y)<1e-12 && onSegment(d,a,b)) || (Math.abs(z)<1e-12 && onSegment(a,c,d)) || (Math.abs(w)<1e-12 && onSegment(b,c,d));
}
export function segmentIntersectsGeometry(a, b, geometry) {
  if (!geometry) return false;
  if (geometry.type === 'GeometryCollection') return geometry.geometries.some(g=>segmentIntersectsGeometry(a,b,g));
  if (pointInGeometry(a,geometry) || pointInGeometry(b,geometry)) return true;
  const polys = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  return polys.some(poly=>poly.some(ring=>ring.some((p,i)=>i>0 && crosses(a,b,ring[i-1],p))));
}
export function bboxGeometry([west,south,east,north]) { return {type:'Polygon',coordinates:[[[west,south],[east,south],[east,north],[west,north],[west,south]]]}; }
function inRing(point, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i], b = ring[j];
    if (onSegment(point, a, b)) return true;
    if ((a[1] > point[1]) !== (b[1] > point[1]) && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}
export function pointInGeometry(point, geometry) {
  if (!geometry) return false;
  if (geometry.type === 'GeometryCollection') return geometry.geometries.some(g => pointInGeometry(point, g));
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  return polygons.some(rings => inRing(point, rings[0]) && !rings.slice(1).some(ring => inRing(point, ring)));
}
export function geometryCenter(geometry) {
  if (!geometry) return null;
  if (geometry.type === 'Point') return geometry.coordinates.slice(0, 2);
  if (geometry.type === 'GeometryCollection') return geometryCenter(geometry.geometries[0]);
  let coordinates = geometry.coordinates;
  if (!coordinates?.length) return null;
  while (Array.isArray(coordinates[0]?.[0])) coordinates = coordinates.flat();
  const lons = coordinates.map(c => c[0]), lats = coordinates.map(c => c[1]);
  return [(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
}
export function matchFacilities(incident, facilities, radiusKm = 10) {
  if (!incident.center) return [];
  const polygon = ['Polygon', 'MultiPolygon', 'GeometryCollection'].includes(incident.geometry?.type);
  const importance = { hospital: 5, clinic: 4, school: 3, fire_station: 2, community_centre: 1, library: 1 };
  return facilities.map(f => ({ ...f, distanceKm: distanceKm(incident.center, f.coordinates) }))
    .filter(f => f.distanceKm <= radiusKm && (!polygon || pointInGeometry(f.coordinates, incident.geometry)))
    .sort((a, b) => (importance[b.kind] || 0) - (importance[a.kind] || 0) || a.distanceKm - b.distanceKm);
}
