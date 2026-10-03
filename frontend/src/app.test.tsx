import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import fixture from './__fixtures__/demo.json'
import { api, request, statusSchema, destinationsSchema, routeResponseSchema } from './api'
import { StatusBar, HazardStatus, RouteComparison, DestinationList } from './components'
import { loadSaved, saveView, needsRevalidation } from './storage'
import App from './App'
vi.mock('./MapView',()=>({default:()=> <div aria-label="Route map">Map geometry</div>}))
const status=statusSchema.parse(fixture.status)
const route=routeResponseSchema.parse(fixture.route)
const destinations=destinationsSchema.parse(fixture.destinations)
beforeEach(()=>{vi.restoreAllMocks()})

describe('API contract',()=>{
 it('parses captured Python responses',()=>{expect(status.demo).toBe(true);expect(route.comparison.safer.route?.route_node_ids).toEqual([1,4,3])})
 it('rejects incomplete responses',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({})}));await expect(request('/status',statusSchema)).rejects.toThrow('incompatible')})
 it('rejects malformed geometry',()=>{const raw=structuredClone(fixture.status);raw.hazards[0].geometry.coordinates=[];expect(statusSchema.safeParse(raw).success).toBe(false)})
 it('does not show server exception text',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,json:async()=>({detail:'Traceback: private error'})}));await expect(request('/status',statusSchema)).rejects.toThrow('request could not be completed')})
 it('handles backend outage',async()=>{vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('Failed')));await expect(request('/status',statusSchema)).rejects.toThrow('Backend unavailable')})
})

describe('route and trust UI',()=>{
 it('renders dynamic distances, avoidance, and deterministic evidence',()=>{render(<RouteComparison result={route}/>);expect(screen.getByText('0.90 km')).toBeVisible();expect(screen.getByText('1.10 km')).toBeVisible();expect(screen.getByText(/1 affected segment avoided/)).toBeVisible();fireEvent.click(screen.getByText('Why this route?'));expect(screen.getByText('Excessive Heat Warning')).toBeVisible();expect(screen.getAllByText(/NOAA\/NWS/).length).toBe(4)})
 it('shows absent route',()=>{const r=structuredClone(route);r.comparison.safer={exists:false,route:null,reason:'No path'};render(<RouteComparison result={r}/>);expect(screen.getByText('No route available')).toBeVisible()})
 it.each(['LIVE','CACHED','MIXED'])('shows backend mode %s without guessing',mode=>{render(<StatusBar status={{...status,demo:false,data_state:{...status.data_state,mode}}} offline={false} saved={false} checking={false}/>);expect(screen.getByText(mode)).toBeVisible()})
 it('shows offline and stale together',()=>{const s=structuredClone(status);Object.values(s.data_state.sources).forEach(source=>source.fetch_freshness='stale');render(<StatusBar status={s} offline saved checking={false}/>);expect(screen.getByText('OFFLINE')).toBeVisible();expect(screen.getByText('STALE')).toBeVisible()})
 it.each(['NO APPLICABLE ACTIVE ALERT','UNAVAILABLE','INCOMPLETE','STALE','EXPIRED'])('reflects hazard status %s',value=>{const s={...status,hazard_categories:[{...status.hazard_categories[0],status:value,active_count:0}]};render(<HazardStatus status={s} saved={false}/>);expect(screen.getByText(value.toLowerCase())).toBeVisible();expect(screen.queryByText('Safe')).toBeNull()})
 it('keeps official and potential classifications separate',()=>{render(<DestinationList data={destinations} selected="center-a" onSelect={()=>{}}/>);expect(screen.getByText('Official cooling center')).toBeVisible();expect(screen.getByText('Potential relief location')).toBeVisible();expect(screen.getAllByText(/Hours, cooling and capacity unverified/)).toHaveLength(2)})
 it('handles empty destinations',()=>{render(<DestinationList data={{...destinations,candidates:[]}} selected="" onSelect={()=>{}}/>);expect(screen.getByText(/No destinations available/)).toBeVisible()})
})

describe('downloaded state',()=>{
 it('round trips validated browser state',()=>{expect(saveView({version:1,saved_at:'2026-10-03T23:00:00Z',status,origin:status.origin,destinations,route})).toBe(true);expect(loadSaved()?.route?.route_id).toBe(route.route_id)})
 it('rejects corrupt state',()=>{localStorage.setItem('wayfinder.download.v1','{broken');expect(loadSaved()).toBeNull()})
 it('flags old real evidence for backend revalidation',()=>{expect(needsRevalidation({...status,demo:false},Date.parse(status.data_state.evaluated_at)+61000)).toBe(true);expect(needsRevalidation(status,Date.now())).toBe(false)})
})

describe('application',()=>{
 it('renders demo through the same API flow',async()=>{vi.spyOn(api,'status').mockResolvedValue(status);vi.spyOn(api,'destinations').mockResolvedValue(destinations);vi.spyOn(api,'route').mockResolvedValue(route);render(<App/>);await screen.findByText('DEMO MODE');await waitFor(()=>expect(screen.getByRole('button',{name:/Compare routes/})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:/Compare routes/}));await screen.findByText('1.10 km');expect(screen.getByText(/All events and destinations are synthetic/)).toBeVisible()})
 it('shows connection error and retry on first visit',async()=>{vi.spyOn(api,'status').mockRejectedValue(new Error('Backend unavailable.'));render(<App/>);expect(await screen.findByRole('alert')).toHaveTextContent('Backend unavailable');expect(screen.getByRole('button',{name:'Retry connection'})).toBeVisible()})
 it('retains downloaded route when backend is gone',async()=>{saveView({version:1,saved_at:'2026-10-03T23:00:00Z',status,origin:status.origin,destinations,route});vi.spyOn(api,'status').mockRejectedValue(new Error('Backend unavailable.'));render(<App/>);await screen.findByRole('alert');expect(screen.getByText('1.10 km')).toBeVisible();expect(screen.getByText(/Displayed route has not been revalidated/)).toBeVisible()})
})

it('does not claim a downloaded view when no snapshot exists',()=>{
 render(<StatusBar status={null} offline saved checking={false}/>);
 expect(screen.getByText('UNAVAILABLE')).toBeVisible();expect(screen.queryByText('CACHED VIEW')).toBeNull()
})
