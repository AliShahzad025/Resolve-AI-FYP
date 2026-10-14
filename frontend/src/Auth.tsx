import { useState, type FormEvent } from 'react';
import { ArrowRight, ArrowUpRight, Check, Eye, EyeOff, ShieldCheck } from 'lucide-react';

export default function Auth({onEnter}: {onEnter:(name:string)=>void}) {
  const [register,setRegister]=useState(false);
  const [show,setShow]=useState(false);
  const [error,setError]=useState('');
  const [name,setName]=useState('');
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  function submit(e:FormEvent) {
    e.preventDefault();
    if(password.length<8) {setError('Use at least 8 characters for your password.');return;}
    setError(''); onEnter(register ? name : email.split('@')[0]);
  }
  return <div className="auth-layout">
    <div className="auth-story"><a className="brand light" href="#/login"><span className="brand-symbol">r<span/></span>resolve<span className="brand-ai">AI</span></a>
      <div className="auth-story-body"><div className="auth-kicker">A LITTLE MORE CONTEXT. A BETTER RESPONSE.</div><h1>Good support<br/>starts with<br/><em>understanding.</em></h1><p>Bring customer conversations, order insights, and your store’s policies into one thoughtful workspace.</p>
      <div className="auth-preview"><div className="auth-preview-top"><span className="tiny-dot"/> A clearer picture of every case <ArrowUpRight size={17}/></div><div className="preview-message">“Maine medium order kiya tha lekin large aya hai.”</div><div className="preview-tags"><span>Roman Urdu</span><span>Wrong item</span><span>Needs attention</span></div><div className="preview-line"><Check size={16}/> Order context and relevant policy, together.</div></div></div>
      <div className="auth-footer">BUILT FOR THE WAY YOUR CUSTOMERS SPEAK.<span>EN / اردو</span></div>
    </div>
    <div className="auth-main"><div className="auth-topline">{register?'Already have an account?':'New to Resolve?'} <button onClick={()=>{setRegister(!register);setError('');}}>{register?'Sign in':'Create an account'}<ArrowUpRight size={14}/></button></div>
      <div className="auth-form-wrap"><div className="eyebrow">YOUR SUPPORT WORKSPACE</div><h2>{register?'A fresh start for your support.':'Welcome back.'}</h2><p>{register?'Create your account and bring your team together.':'Sign in to pick up where your team left off.'}</p>
        <form onSubmit={submit} className="auth-form">{register && <label>Full name<input autoComplete="name" placeholder="Your full name" required value={name} onChange={e=>setName(e.target.value)}/></label>}
          <label>Work email<input type="email" autoComplete="email" placeholder="you@yourstore.com" required value={email} onChange={e=>setEmail(e.target.value)}/></label>
          <label>Password<div className="password-field"><input type={show?'text':'password'} autoComplete={register?'new-password':'current-password'} placeholder="At least 8 characters" required value={password} onChange={e=>setPassword(e.target.value)}/><button type="button" aria-label={show?'Hide password':'Show password'} onClick={()=>setShow(!show)}>{show?<EyeOff size={18}/>:<Eye size={18}/>}</button></div></label>
          {error && <div role="alert" className="form-error">{error}</div>}
          <button className="button primary auth-submit">{register?'Create account':'Sign in'}<ArrowRight size={17}/></button>
        </form>
        <div className="auth-divider"><span/>or explore first<span/></div><button className="button demo-button" onClick={()=>onEnter('Muhammad Ali')}>Open sample workspace<ArrowUpRight size={16}/></button>
        <p className="auth-note"><ShieldCheck size={15}/> Interactive UI preview. No account is created and no password is stored.</p>
      </div><div className="auth-bottom">Resolve AI <span>Customer care, with context.</span></div>
    </div>
  </div>;
}
