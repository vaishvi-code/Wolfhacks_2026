import {DatabaseSync} from 'node:sqlite';
import {mkdirSync} from 'node:fs';
import {dirname} from 'node:path';
export class Store {
  constructor(path='data/terrawatch.sqlite') {
    if(path!==':memory:') mkdirSync(dirname(path),{recursive:true});
    this.db=new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS readings (station TEXT NOT NULL,time TEXT NOT NULL,value REAL NOT NULL,provisional INTEGER NOT NULL,PRIMARY KEY(station,time));
      CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY,region TEXT NOT NULL,mode TEXT NOT NULL,expires TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS road_events (id TEXT PRIMARY KEY,region TEXT NOT NULL,mode TEXT NOT NULL,created TEXT NOT NULL,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS delivery (destination TEXT NOT NULL,station TEXT NOT NULL,time TEXT NOT NULL,PRIMARY KEY(destination,station,time));`);
  }
  get(key){const row=this.db.prepare('SELECT * FROM cache WHERE key=?').get(key);return row?{data:JSON.parse(row.value),updated:row.updated}:null;}
  set(key,value){this.db.prepare('INSERT INTO cache VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated').run(key,JSON.stringify(value),new Date().toISOString());}
  saveReadings(station,readings){
    const stmt=this.db.prepare('INSERT INTO readings VALUES(?,?,?,?) ON CONFLICT(station,time) DO UPDATE SET value=excluded.value,provisional=excluded.provisional');
    this.db.exec('BEGIN');try{for(const r of readings)stmt.run(station,r.time,r.value,+r.provisional);this.db.exec('COMMIT');}catch(e){this.db.exec('ROLLBACK');throw e;}
    const cutoff=new Date(Date.now()-7*86400000).toISOString();
    this.db.prepare('DELETE FROM readings WHERE time < ?').run(cutoff);
    this.db.prepare('DELETE FROM delivery WHERE time < ?').run(cutoff);
  }
  readings(station){return this.db.prepare('SELECT time,value,provisional FROM readings WHERE station=? AND time>=? ORDER BY time').all(station,new Date(Date.now()-27*3600000).toISOString()).map(r=>({...r,provisional:!!r.provisional}));}
  addReport(r){const event={...r,createdAt:r.createdAt||new Date().toISOString()};this.db.exec('BEGIN');try{this.db.prepare('INSERT INTO reports VALUES(?,?,?,?,?)').run(r.id,r.region,r.mode,r.expires,JSON.stringify(event));this.db.prepare('INSERT INTO road_events (id,region,mode,created,payload) VALUES(?,?,?,?,?)').run(r.id,r.region,r.mode,event.createdAt,JSON.stringify(event));this.db.exec('COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}}
  roadEvents(region,mode){return this.db.prepare('SELECT payload FROM road_events WHERE region=? AND mode=? ORDER BY created DESC LIMIT 20').all(region,mode).map(r=>JSON.parse(r.payload));}
  pendingEvents(){return this.db.prepare('SELECT payload FROM road_events WHERE delivered=0 ORDER BY created LIMIT 100').all().map(r=>JSON.parse(r.payload));}
  eventDelivered(id){this.db.prepare('UPDATE road_events SET delivered=1 WHERE id=?').run(id);}
  resolveDemoEvents(region){
    const rows=this.db.prepare("SELECT payload FROM road_events WHERE region=? AND mode='demo'").all(region);let count=0;
    for(const row of rows){const e=JSON.parse(row.payload);if(!e.simulation||e.status==='resolved')continue;e.status='resolved';e.expires=new Date().toISOString();const payload=JSON.stringify(e);this.db.prepare('UPDATE road_events SET payload=?,delivered=0 WHERE id=?').run(payload,e.id);this.db.prepare('UPDATE reports SET payload=?,expires=? WHERE id=?').run(payload,e.expires,e.id);count++;}
    return count;
  }
  reports(region,mode){this.db.prepare('DELETE FROM reports WHERE expires<=?').run(new Date().toISOString());return this.db.prepare('SELECT payload FROM reports WHERE region=? AND mode=? ORDER BY expires DESC').all(region,mode).map(r=>JSON.parse(r.payload));}
  pending(destination,station){return this.db.prepare('SELECT r.* FROM readings r LEFT JOIN delivery d ON d.station=r.station AND d.time=r.time AND d.destination=? WHERE r.station=? AND d.time IS NULL ORDER BY r.time LIMIT 1000').all(destination,station);}
  delivered(destination,station,time){this.db.prepare('INSERT OR IGNORE INTO delivery VALUES(?,?,?)').run(destination,station,time);}
  close(){this.db.close();}
}
