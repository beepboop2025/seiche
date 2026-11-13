import {useEffect, useState} from 'react';
import {API_BASE} from '../apiBase';
import type {Row} from './deskModel';
const cache = new Map<string, {until:number; value:Promise<Row>}>();
export function readDesk(path:string): Promise<Row> {
  const prior = cache.get(path); if (prior && prior.until > Date.now()) return prior.value;
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 180000);
  const value = fetch(API_BASE + path, {credentials:'omit',signal:controller.signal,headers:{Accept:'application/json'}})
    .then(async response => {if (!response.ok) throw new Error(`Source unavailable (HTTP ${response.status}).`); const result = await response.json(); if (!result || typeof result !== 'object') throw new Error('Source response could not be read.'); return result;})
    .catch(error => {cache.delete(path); throw error;}).finally(() => clearTimeout(timer));
  cache.set(path,{until:Date.now()+60000,value}); return value;
}
export function useDesk(path:string|null) {
  const [state,setState] = useState<{path:string|null;data:Row|null;error:string|null}>({path,data:null,error:null});
  useEffect(() => {let active = true; if (path) {setState({path,data:null,error:null}); void readDesk(path).then(data => {if(active)setState({path,data,error:null});}).catch(error => {if(active)setState({path,data:null,error:String(error.message)});});} return () => {active=false;};},[path]);
  return state.path === path ? state : {path,data:null,error:null};
}
