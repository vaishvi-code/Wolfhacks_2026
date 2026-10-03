import { z } from 'zod'

const position=z.tuple([z.number().finite().min(-180).max(180),z.number().finite().min(-90).max(90)])
const line=z.array(position).min(2)
const polygon=z.array(z.array(position).min(4)).min(1)
const geometry=z.discriminatedUnion('type',[
  z.object({type:z.literal('Point'),coordinates:position}),
  z.object({type:z.literal('LineString'),coordinates:line}),
  z.object({type:z.literal('Polygon'),coordinates:polygon}),
  z.object({type:z.literal('MultiPolygon'),coordinates:z.array(polygon)}),
  z.object({type:z.literal('MultiLineString'),coordinates:z.array(line)}),
])
const collection = z.object({ type: z.literal('FeatureCollection'), features: z.array(z.object({ type: z.literal('Feature'), geometry, properties: z.record(z.unknown()).nullable() }).passthrough()) })
const metadata = z.object({ event: z.string().optional(), freshness: z.string().optional(), expires: z.string().nullable().optional(), fetched_at: z.string().nullable().optional(), reason: z.string().optional() }).passthrough()
const hazard = z.object({ id:z.string(), hazard_type:z.string(), geometry, source:z.string(), severity:z.string(), metadata })
const source = z.object({ status:z.string(), fetch_freshness:z.string(), fetched_at:z.string().nullable().optional(), issues:z.array(z.string()).optional() }).passthrough()
export const dataStateSchema = z.object({ mode:z.string(), evaluated_at:z.string(), snapshot_created_at:z.string().nullable(), hazard_coverage_state:z.string(), sources:z.record(source), warnings:z.array(z.unknown()).optional(), cache_error:z.unknown().optional() }).passthrough()
export const statusSchema = z.object({ api_version:z.literal(1), workspace_id:z.string(), demo:z.boolean(), demo_stage:z.number(), offline:z.boolean(), origin:z.tuple([z.number(),z.number()]), data_state:dataStateSchema, hazards:z.array(hazard), context:z.array(z.record(z.unknown())), roads:collection,
  hazard_categories:z.array(z.object({ id:z.string(), label:z.string(), status:z.string(), active_count:z.number(), mapped_count:z.number(), data_origin:z.string() })) })
const contribution = z.object({ hazard_id:z.string(), hazard_type:z.string(), source:z.string(), freshness:z.string(), risk_level:z.string(), reason:z.string(), evidence:metadata }).passthrough()
const roadRisk = z.object({ hazard_ids:z.array(z.string()), contributions:z.array(contribution), risk_level:z.string() }).passthrough()
export const routeSchema = z.object({ total_distance_m:z.number(), route_node_ids:z.array(z.number()), route_geometry:geometry, affected_edge_count:z.number(), total_risk_penalty:z.number(), hazard_ids:z.array(z.string()), road_risks:z.array(roadRisk) }).passthrough()
const routeOption = z.object({ exists:z.boolean(), route:routeSchema.nullable(), reason:z.string().nullable() }).refine(value=>value.exists === (value.route!==null), 'Inconsistent route availability')
export const comparisonSchema = z.object({ baseline:routeOption, safer:routeOption, avoided_edge_count:z.number().nullable(), avoided_hazard_ids:z.array(z.string()).nullable(), data_state:dataStateSchema }).passthrough()
export const destinationSchema = z.object({ id:z.string(), name:z.string().nullable(), latitude:z.number(), longitude:z.number(), categories:z.array(z.string()), source:z.string(), classification:z.enum(['official_cooling_center','potential_heat_relief_location']), designation_freshness:z.string(), geometry }).passthrough()
export const destinationsSchema = z.object({ selected_destination_id:z.string().nullable(), candidates:z.array(z.object({ destination:destinationSchema.optional(), eligible:z.boolean(), exclusion_reasons:z.array(z.string()), comparison:comparisonSchema.optional(), rank:z.number().optional() }).passthrough()), warnings:z.array(z.string()), ranking_order:z.array(z.string()) }).passthrough()
export const routeResponseSchema = z.object({ route_id:z.string(), comparison:comparisonSchema, destination:destinationSchema, destination_eligible:z.boolean().optional(), reevaluation:z.object({ route_updated:z.boolean(), alternative_status:z.string(), reason_codes:z.array(z.string()) }).passthrough().optional() })
export type Status = z.infer<typeof statusSchema>
export type Destinations = z.infer<typeof destinationsSchema>
export type RouteResponse = z.infer<typeof routeResponseSchema>
export type Route = z.infer<typeof routeSchema>
export type Destination = z.infer<typeof destinationSchema>
export type Location = [number,number]

export class ApiError extends Error { constructor(message:string, public code='UNAVAILABLE') { super(message) } }
export async function request<T>(path:string, schema:z.ZodType<T>, body?:unknown):Promise<T> {
  let response:Response
  try { response = await fetch(`/api${path}`, { method: body === undefined ? 'GET':'POST', headers: {'Content-Type':'application/json'}, body:body === undefined ? undefined:JSON.stringify(body), cache:'no-store', signal:AbortSignal.timeout(120_000) }) }
  catch { throw new ApiError('Backend unavailable. Downloaded routes remain viewable; new routes need a reachable local backend.') }
  let raw:unknown
  try { raw=await response.json() } catch { throw new ApiError('The backend returned an unreadable response. Try again.','INVALID_RESPONSE') }
  if (!response.ok) {
    const parsed=z.object({error:z.object({code:z.string(),message:z.string()})}).safeParse(raw)
    throw new ApiError(parsed.success ? parsed.data.error.message : 'The request could not be completed.', parsed.success ? parsed.data.error.code:'REQUEST_FAILED')
  }
  const result=schema.safeParse(raw)
  if (!result.success) throw new ApiError('The backend response is incompatible. Your downloaded view has been kept.','INVALID_RESPONSE')
  return result.data
}
export const api = {
  status:()=>request('/status',statusSchema),
  destinations:(origin:Location)=>request('/destinations',destinationsSchema,{origin:{latitude:origin[0],longitude:origin[1]}}),
  route:(origin:Location,destination_id:string)=>request('/route',routeResponseSchema,{origin:{latitude:origin[0],longitude:origin[1]},destination_id}),
  offline:()=>request('/offline',statusSchema,{}),
  refresh:()=>request('/refresh',statusSchema,{}),
  reevaluate:(route_id:string)=>request('/route/reevaluate',routeResponseSchema,{route_id}),
  demo:(action:'offline'|'reconnect'|'reset')=>request('/demo',statusSchema,{action}),
}
