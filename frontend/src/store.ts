import { useState } from 'react';

/** Local demo repository. Replace these state boundaries with authenticated API calls. */
export function usePersisted<T>(key: string, initial: T) {
  const [value,setValue] = useState<T>(()=>{
    try { const stored=localStorage.getItem(`resolve:v1:${key}`); return stored ? JSON.parse(stored) as T : initial; }
    catch { return initial; }
  });
  const update = (next: T | ((prev:T)=>T)) => setValue(prev=>{
    const result=typeof next==='function' ? (next as (prev:T)=>T)(prev) : next;
    try { localStorage.setItem(`resolve:v1:${key}`,JSON.stringify(result)); } catch { /* Keep the current session usable if storage is full. */ }
    return result;
  });
  return [value,update] as const;
}
