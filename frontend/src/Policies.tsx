import { useEffect, useRef, useState } from 'react';
import { BookOpen, Plus, Upload, Check, FileText, History, Save, ArrowUpRight, Search, RotateCcw, AlertCircle, Sparkles } from 'lucide-react';
import { Badge, Empty, Modal, PageHeader } from './components';
import { request, type ApiPolicy, type PoliciesResponse, type PolicySearchResponse, type RetrievalStatus } from './api';

const INDEX_LABEL = { pending: 'Indexing…', ready: 'Indexed', failed: 'Indexing failed' } as const;

export default function Policies({ token, role, notify }: { token: string; role: string; notify: (s: string) => void }) {
  const canEdit = role === 'Owner' || role === 'Manager';
  const [policies, setPolicies] = useState<ApiPolicy[]>([]);
  const [retrieval, setRetrieval] = useState<RetrievalStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState('');            // policy title
  const [view, setView] = useState<number | null>(null);    // version being viewed (null = latest)
  const [content, setContent] = useState('');
  const [newTitle, setNewTitle] = useState<string | null>(null); // unsaved new policy
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<PolicySearchResponse | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const policy = policies.find(p => p.title === selected);
  const version = policy?.versions.find(v => v.version === (view ?? policy.versions.at(-1)?.version));
  const isNew = newTitle !== null && selected === newTitle && !policy;
  const dirty = isNew ? content.trim().length > 0 : content !== (version?.content ?? '');
  const pending = policies.some(p => p.versions.some(v => v.index_status === 'pending'));

  function apply(data: PoliciesResponse, keep?: { title: string; version?: number }) {
    setPolicies(data.policies);
    if (data.retrieval) setRetrieval(data.retrieval);
    if (keep) {
      const next = data.policies.find(p => p.title.toLowerCase() === keep.title.toLowerCase());
      if (next) {
        setSelected(next.title); setNewTitle(null); setView(keep.version ?? null);
        setContent((next.versions.find(v => v.version === keep.version) ?? next.versions.at(-1))!.content);
      }
    }
  }

  async function load(first = false) {
    try {
      const data = await request<PoliciesResponse>('/api/policies', {}, token);
      setPolicies(data.policies); if (data.retrieval) setRetrieval(data.retrieval);
      if (first && data.policies[0]) { setSelected(data.policies[0].title); setView(null); setContent(data.policies[0].versions.at(-1)!.content); }
    } catch (err) { if (first) notify(err instanceof Error ? err.message : 'Could not load policies.'); }
    finally { setLoaded(true); }
  }
  useEffect(() => { void load(true); }, [token]);
  // While a version is being indexed, refresh so its status turns to "Indexed" without a reload.
  useEffect(() => { if (!pending) return; const timer = setInterval(() => void load(), 3000); return () => clearInterval(timer); }, [pending, token]);

  function choose(p: ApiPolicy) { setSelected(p.title); setNewTitle(null); setView(null); setContent(p.versions.at(-1)!.content); }

  async function act(path: string, success: string, keep?: { title: string; version?: number }, body?: unknown) {
    setBusy(true);
    try {
      const data = await request<PoliciesResponse & { version?: number; added?: number }>(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) }, token);
      apply(data, keep ? { ...keep, version: data.version ?? keep.version } : undefined);
      notify(data.added !== undefined ? (data.added ? `${data.added} starter policies added. Review them, then activate.` : 'Starter policies are already in your library.') : success);
      return data;
    } catch (err) { notify(err instanceof Error ? err.message : 'That did not work. Please try again.'); return null; }
    finally { setBusy(false); }
  }

  async function save() {
    const policyTitle = isNew ? newTitle! : policy?.title;
    if (!policyTitle || !content.trim()) return;
    if (content.trim().length < 20) { notify('Write at least a sentence or two before saving.'); return; }
    await act('/api/policies', 'New version saved. It is being indexed; activate it when it shows “Indexed”.', { title: policyTitle }, { title: policyTitle, content: content.trim() });
  }

  async function runSearch() {
    if (!query.trim()) return;
    setSearching(true);
    try { setResults(await request<PolicySearchResponse>('/api/policies/search', { method: 'POST', body: JSON.stringify({ query: query.trim() }) }, token)); }
    catch (err) { notify(err instanceof Error ? err.message : 'Search failed.'); }
    finally { setSearching(false); }
  }

  const retrievalProblem = retrieval && (retrieval.embedding.state === 'unavailable' || retrieval.embedding.state === 'error' ? retrieval.embedding.error : retrieval.vector_store.error);

  return <>
    <PageHeader eyebrow="KNOWLEDGE & GUIDANCE" title="Your policy library" subtitle="Consistent answers start with a clear source of truth."
      actions={canEdit ? <button className="button primary" onClick={() => setAdding(true)}><Plus size={16} />New policy</button> : undefined} />
    {retrievalProblem && <p className="policy-notice" role="status"><AlertCircle size={15} />Policy search is not ready: {retrievalProblem}</p>}

    <div className="policy-layout">
      <aside className="policy-nav">
        <div className="section-label">STORE DOCUMENTS <span>{policies.length}</span></div>
        {policies.map(item => <button key={item.title} className={`policy-nav-item ${selected === item.title ? 'selected' : ''}`} onClick={() => choose(item)}>
          <BookOpen size={19} /><span><strong>{item.title}</strong><small>{item.active_version ? 'Active · v' + item.active_version : 'Not active'}</small></span>
        </button>)}
        {isNew && <button className="policy-nav-item selected"><BookOpen size={19} /><span><strong>{newTitle}</strong><small>Unsaved</small></span></button>}
        {loaded && !policies.length && !isNew && <div className="policy-empty">
          <p>No policies yet. Suggested replies need at least one active policy.</p>
          {canEdit && <button className="button" disabled={busy} onClick={() => void act('/api/policies/starter', '', undefined)}><Sparkles size={14} />Add starter policies</button>}
        </div>}
        <div className="policy-help"><div className="policy-help-icon"><BookOpen size={25} strokeWidth={1.2} /></div><h3>Grounded in your words.</h3>
          <p>Suggested replies quote only the active version of each policy. Every saved version is kept.</p></div>
      </aside>

      {(policy || isNew) ? <section className="panel policy-editor">
        <div className="policy-editor-heading">
          <div><h2>{isNew ? newTitle : policy!.title}</h2><p>{isNew ? 'Write the policy, then save version 1.' : `${policy!.versions.length} saved version${policy!.versions.length === 1 ? '' : 's'}`}</p></div>
          <Badge tone={version?.active ? 'active' : 'neutral'}>{isNew ? 'Unsaved' : version?.active ? 'Active' : 'Not active'}</Badge>
        </div>
        <div className="policy-toolbar">
          <span><FileText size={15} />{isNew ? 'Version 1' : `Version ${version?.version}`}{dirty && <i className="unsaved-label">Unsaved changes</i>}</span>
          {canEdit && <><input ref={fileRef} type="file" accept=".txt,text/plain" hidden onChange={async e => {
            const file = e.target.files?.[0]; if (!file) return;
            if (file.size > 1024 * 1024) { notify('Choose a text document smaller than 1 MB.'); return; }
            setContent(await file.text()); notify('Text imported. Review it before saving a new version.'); e.target.value = '';
          }} /><button className="text-button" onClick={() => fileRef.current?.click()}><Upload size={14} />Import text</button></>}
        </div>
        <label className="sr-only" htmlFor="policy-content">Policy content</label>
        <textarea id="policy-content" className="policy-textarea" value={content} readOnly={!canEdit} onChange={e => setContent(e.target.value)}
          placeholder={'Section heading\nWrite each rule as a clear sentence. Leave a blank line between sections.'} />
        <div className="policy-editor-footer">
          <span>{!isNew && version && <>
            <span className={`live-dot ${version.index_status === 'ready' ? '' : 'offline'}`} />
            {INDEX_LABEL[version.index_status]}{version.index_status === 'ready' && ` · ${version.passages} passage${version.passages === 1 ? '' : 's'}`}
            {version.index_status === 'failed' && canEdit && <button className="text-button" disabled={busy} onClick={() => void act(`/api/policies/${version.id}/reindex`, 'Indexing restarted.', { title: policy!.title, version: version.version })}><RotateCcw size={13} />Retry</button>}
          </>}</span>
          {canEdit && <div>
            {!isNew && version && (version.active
              ? <button className="button" disabled={busy || dirty} onClick={() => void act(`/api/policies/${version.id}/deactivate`, `Version ${version.version} is no longer used for replies.`, { title: policy!.title, version: version.version })}>Deactivate</button>
              : <button className="button" disabled={busy || dirty || version.index_status !== 'ready'} title={version.index_status !== 'ready' ? 'Available once indexing has finished' : undefined}
                  onClick={() => void act(`/api/policies/${version.id}/activate`, `Version ${version.version} is now the active policy.`, { title: policy!.title, version: version.version })}><Check size={15} />Activate version</button>)}
            <button className="button primary" disabled={busy || !dirty || !content.trim()} onClick={() => void save()}><Save size={15} />{isNew ? 'Save version 1' : 'Save new version'}</button>
          </div>}
        </div>
        {!isNew && policy && <div className="version-history"><h3><History size={16} />Version history</h3>
          {[...policy.versions].reverse().map(v => <button className={`version-row ${version?.version === v.version ? 'viewing' : ''}`} key={v.id} onClick={() => { setView(v.version); setContent(v.content); }}>
            <span className="version-icon"><FileText size={16} /></span>
            <span><strong>Version {v.version}</strong><small>{v.uploaded_at.slice(0, 10)} · {v.content.length} characters · {INDEX_LABEL[v.index_status]}</small></span>
            {v.active ? <Badge tone="active">Active</Badge> : <span className="muted">Saved</span>}<ArrowUpRight size={15} />
          </button>)}
        </div>}
      </section> : <section className="panel policy-editor"><Empty title={loaded ? 'Choose or create a policy' : 'Loading policies…'} description={loaded ? 'Policies tell the assistant what your store promises.' : ''} /></section>}
    </div>

    <section className="panel retrieval-test">
      <div className="panel-heading"><h3>Try a customer question</h3><span className="muted">See which policy passages the assistant would use</span></div>
      <form className="retrieval-form" onSubmit={e => { e.preventDefault(); void runSearch(); }}>
        <div className="search-field"><Search size={15} /><input aria-label="Customer question" value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. Mera parcel toota hua aaya, paise wapis chahiye" /></div>
        <button className="button" disabled={searching || !query.trim()}>{searching ? 'Searching…' : 'Search policies'}</button>
      </form>
      {results && (results.state === 'ok'
        ? <div className="retrieval-results">{results.passages.map(p => <div className="evidence-card" key={p.chunk_id}>
            <div>{p.title} <span>v{p.version}</span>{p.score !== null && <span className="evidence-score">{Math.round(p.score * 100)}% match</span>}</div><p>{p.text}</p></div>)}</div>
        : <p className="retrieval-message"><AlertCircle size={14} />{results.message}{results.best_score !== null && results.state === 'no_evidence' && ` (best match ${Math.round(results.best_score * 100)}%, minimum ${Math.round(results.min_score * 100)}%)`}</p>)}
    </section>

    {adding && <Modal title="Create a policy" onClose={() => setAdding(false)}>
      <form onSubmit={e => {
        e.preventDefault(); const clean = title.trim();
        const existing = policies.find(p => p.title.toLowerCase() === clean.toLowerCase());
        if (existing) { choose(existing); notify('That policy already exists. Edit it and save a new version.'); }
        else { setNewTitle(clean); setSelected(clean); setView(null); setContent(''); }
        setTitle(''); setAdding(false);
      }}>
        <div className="modal-body"><p>Give your policy a clear title so your team can find it easily.</p>
          <label>Policy title<input autoFocus required minLength={2} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Returns & exchanges" maxLength={80} /></label></div>
        <div className="modal-actions"><button type="button" className="button" onClick={() => setAdding(false)}>Cancel</button><button className="button primary" disabled={title.trim().length < 2}>Create policy</button></div>
      </form>
    </Modal>}
  </>;
}
