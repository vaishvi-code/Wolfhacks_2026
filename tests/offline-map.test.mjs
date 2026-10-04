import test from 'node:test';
import assert from 'node:assert/strict';
import {roadFeatures} from '../public/offline-map.js';

test('saved graph produces named streets without inventing missing connections',()=>{
  const graph={nodes:[{id:'a',coordinates:[-78,35]},{id:'b',coordinates:[-78.1,35.1]}],edges:[{from:'a',to:'b',name:'Main Street'},{from:'a',to:'missing'}]};
  const original=structuredClone(graph),result=roadFeatures(graph);
  assert.equal(result.features.length,1);
  assert.deepEqual(result.features[0].geometry.coordinates,[[-78,35],[-78.1,35.1]]);
  assert.equal(result.features[0].properties.name,'Main Street');
  assert.deepEqual(graph,original);
  assert.deepEqual(roadFeatures(null).features,[]);
});
