import { z } from 'zod'
import { statusSchema, destinationsSchema, routeResponseSchema } from './api'
export const savedSchema=z.object({version:z.literal(1),saved_at:z.string(),status:statusSchema,origin:z.tuple([z.number(),z.number()]),destinations:destinationsSchema.nullable(),route:routeResponseSchema.nullable()})
export type Saved=z.infer<typeof savedSchema>
const key='wayfinder.download.v1'
export function loadSaved():Saved|null {
  try {const result=savedSchema.safeParse(JSON.parse(localStorage.getItem(key)||'null'));return result.success?result.data:null} catch {return null}
}
export function saveView(value:Saved):boolean {
  try {localStorage.setItem(key,JSON.stringify(value));return true} catch {return false}
}
// Display-only expiry warning; no browser routing or hazard-policy evaluation.
export function needsRevalidation(status:Saved['status'], now=Date.now()) {
  if(status.demo) return false // Explicitly labeled fixed scenario clock.
  return now-Date.parse(status.data_state.evaluated_at)>60_000
}
