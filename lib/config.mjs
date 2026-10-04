export const REGIONS = {
  raleigh: { id:'raleigh', name:'Raleigh', county:'Wake County', fips:'183', center:[-78.6382,35.7796], bbox:[-78.7182,35.7196,-78.5582,35.8396], station:'KRDU', gauge:'02087500', gaugeName:'Neuse River near Clayton', gaugeCoordinates:[-78.40528,35.64722] },
  wilmington: { id:'wilmington', name:'Wilmington', county:'New Hanover County', fips:'129', center:[-77.9447,34.2257], bbox:[-78.0247,34.1657,-77.8647,34.2857], station:'KILM', gauge:'02105769', gaugeName:'Cape Fear River at Wilmington', gaugeCoordinates:[-77.95389,34.22778] },
  asheville: { id:'asheville', name:'Asheville', county:'Buncombe County', fips:'021', center:[-82.5515,35.5951], bbox:[-82.6315,35.5351,-82.4715,35.6551], station:'KAVL', gauge:'03451500', gaugeName:'French Broad River at Asheville', gaugeCoordinates:[-82.57861,35.60861] }
};
export const POLL_MS = Math.max(60_000, Number(process.env.POLL_MS)||60_000);
export const USER_AGENT = process.env.DATA_USER_AGENT || 'WayAhead/1.0 (student geospatial disaster-planning prototype)';
export const DISCLAIMER = 'Planning prototype. Routes and facility availability are unverified. Follow local emergency officials; call 911 for an emergency.';
