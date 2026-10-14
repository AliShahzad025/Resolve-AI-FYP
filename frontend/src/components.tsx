import { useEffect, useRef, type ReactNode } from 'react';
import { X, Inbox, ArrowUpRight, ChevronDown } from 'lucide-react';
import type { Tier } from './types';

export const money = (value: number) => `Rs. ${value.toLocaleString('en-PK')}`;
export const tier = (score: number | null): Tier => score === null ? 'Unavailable' : score >= .7 ? 'High' : score >= .3 ? 'Medium' : 'Low';
export const initials = (name: string) => name.split(' ').map(x => x[0]).slice(0,2).join('');
export function Badge({children, tone = 'neutral'}: {children: ReactNode; tone?: string}) {
  return <span className={`badge ${tone.toLowerCase().replaceAll(' ', '-')}`}><span className="badge-dot" />{children}</span>;
}
export function Avatar({name, small = false}: {name: string; small?: boolean}) {
  return <span className={`avatar ${small ? 'small' : ''}`} aria-hidden="true">{initials(name)}</span>;
}
export function PageHeader({eyebrow, title, subtitle, actions}: {eyebrow?: string; title: string; subtitle: string; actions?: ReactNode}) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1><p>{subtitle}</p></div><div className="heading-actions">{actions}</div></div>;
}
export function Empty({title = 'No results found', description = 'Try changing your search or filters.'}: {title?: string; description?: string}) {
  return <div className="empty"><Inbox size={28} strokeWidth={1.4}/><h3>{title}</h3><p>{description}</p></div>;
}
export function Select({label, value, onChange, options}: {label: string; value: string; onChange: (value:string)=>void; options: string[]}) {
  return <label className="select-wrap"><span className="sr-only">{label}</span><select value={value} onChange={e=>onChange(e.target.value)}>{options.map(o=><option key={o}>{o}</option>)}</select><ChevronDown size={14}/></label>;
}
export function Modal({title, children, onClose, wide = false}: {title: string; children: ReactNode; onClose: ()=>void; wide?: boolean}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=ref.current; node?.showModal(); return ()=>node?.close();},[]);
  return <dialog ref={ref} className={`modal ${wide ? 'wide' : ''}`} onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget) onClose();}}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={20}/></button></div>{children}</dialog>;
}
export function PanelTitle({title, subtitle, action, onAction}: {title:string; subtitle?:string; action?:string; onAction?:()=>void}) {
  return <div className="panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action && <button className="text-button" onClick={onAction}>{action}<ArrowUpRight size={15}/></button>}</div>;
}
export function Toggle({label, description, checked, onChange}: {label:string; description:string; checked:boolean; onChange:(v:boolean)=>void}) {
  return <div className="toggle-row"><div><strong>{label}</strong><p>{description}</p></div><button type="button" role="switch" aria-label={label} aria-checked={checked} className={`switch ${checked?'on':''}`} onClick={()=>onChange(!checked)}><span/></button></div>;
}
export function downloadCSV(name: string, rows: (string|number)[][]) {
  const csv = rows.map(row=>row.map(v=>`"${String(v).replaceAll('"','""')}"`).join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob(['\ufeff',csv],{type:'text/csv;charset=utf-8;'}));
  const anchor=document.createElement('a'); anchor.href=url; anchor.download=name; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
