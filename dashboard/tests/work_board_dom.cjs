/* SPA DOM acceptance with synthetic API fixtures only. */
const {JSDOM}=require('jsdom');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const appRoot=join(__dirname,'..');
const assert = require('node:assert/strict');
const root = '11111111-1111-4111-8111-111111111111';
const receiptId = '22222222-2222-4222-8222-222222222222';
const refs = [{reference_type:'PR',reference_id:'example/project#1'}];
let items = [
 {item_id:'cleanup',title:'Qualify corrected cleanup',state:'OPEN',stance:'ACTIVE',next_action:'Run the reviewed acceptance',owner:'CI maintainer',reason:'Source reviewed; qualification pending',references:refs},
 {item_id:'activation',title:'Enable the one-slot trial',state:'OPEN',stance:'HELD',next_action:'Activate only after explicit approval',owner:'Operator',reason:'Wait for accepted drain and operator approval',references:refs},
 {item_id:'expansion',title:'Expand to other repositories',state:'OPEN',stance:'DEFERRED',next_action:null,owner:'CI maintainer',reason:'Separate follow-up after the trial',references:refs}
];
function brief(){return {contract:'work-brief-v1',initiative:{initiative_id:root,initiative_key:'synthetic-ci-pilot',title:'Disposable CI pilot',objective:'Qualify one isolated runner without disturbing persistent CI.',scope:'One repository and one slot.',work_state:'ACTIVE',priority:'HIGH',version:2},context_token:'a'.repeat(64),checkpoint_status:'RECORDED',checkpoint_withheld:false,checkpoint:{activity_id:receiptId,created_at:'2026-10-22T15:00:00Z',reported_producer:'Synthetic fixture'},items,next_actions:items.filter(x=>x.stance==='ACTIVE'&&x.state==='OPEN').map(x=>x.item_id),held:['activation'],deferred:['expansion'],uncertain:[],recorded_done:items.filter(x=>x.state==='DONE_IN_RECORD').map(x=>x.item_id),paused_actions:[],activities_since_checkpoint:0,recent_activity:[{activity_id:receiptId,activity_type:'NOTE',created_at:'2026-10-22T15:00:00Z',body:'Qualification evidence is ready for review.',author_principal_id:receiptId}],evidence_policy:'Recorded claims; evidence is not live verification.'};}

async function waitFor(test){for(let i=0;i<100;i++){if(test())return;await new Promise(r=>setTimeout(r,5));}throw new Error('DOM condition not reached');}
(async()=>{
 const html=readFileSync(join(appRoot,'static/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
 const dom=new JSDOM(html,{url:'https://docplane-fixture.test/',runScripts:'outside-only'});
 const w=dom.window,d=w.document,errors=[],writes=[];
 w.addEventListener('error',e=>errors.push(e.message));
 w.sessionStorage.setItem('docplane-token','synthetic-fixture');
 w.crypto.randomUUID=()=>require('node:crypto').randomUUID();w.confirm=()=>true;
 w.fetch=async(path,options={})=>{
  const name=String(path).split('?')[0];let data={};
  if(name.endsWith('/discovery'))data={authentication:{token_acquisition:{self_service:false,access_profile:'managed'}}};
  else if(name.endsWith('/capabilities'))data={principal:{display_name:'Synthetic fixture',role:'CONTRIBUTOR'}};
  else if(name.endsWith('/work/queues'))data={by_state:{ACTIVE:1},inbox:0,wip_limit:3};
  else if(name.endsWith('/work/captures'))data={captures:[]};
  else if(name.endsWith('/initiatives'))data={initiatives:[brief().initiative]};
  else if(name.endsWith('/brief'))data=brief();
  else if(name.endsWith('/updates')){
   const payload=JSON.parse(options.body);writes.push({key:options.headers['Idempotency-Key'],payload});
   items=items.map(x=>x.item_id===payload.changes[0].item.item_id?payload.changes[0].item:x);
   if(writes.length===1)throw new Error('Synthetic lost response after commit');
   data={activity_id:receiptId,contract:'work-brief-v1'};
  }else if(name.endsWith('/history'))data={activities:brief().recent_activity};
  return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(data))};
 };
 w.eval(readFileSync(join(appRoot,'static/app.js'),'utf8'));
 await waitFor(()=>d.querySelectorAll('#work-brief .work-item').length===3);
 assert(d.querySelector('#view-work').classList.contains('active'));
 assert.equal(d.querySelectorAll('#work-brief .work-hold').length,2);
 d.querySelector('.work-edit[data-id="cleanup"]').click();
 d.getElementById('work-item-state').value='DONE_IN_RECORD';
 d.getElementById('work-item-reason').value='Synthetic qualification passed. Activation remains held.';
 d.getElementById('work-update-form').dispatchEvent(new w.Event('submit',{cancelable:true}));
 await waitFor(()=>d.getElementById('work-update-status').textContent.includes('Retry Record update'));
 assert(d.getElementById('work-item-title').disabled);
 d.getElementById('work-update-form').dispatchEvent(new w.Event('submit',{cancelable:true}));
 await waitFor(()=>d.getElementById('work-update-panel').hidden);
 assert.equal(writes.length,2);assert.deepEqual(writes[0],writes[1]);assert.match(writes[0].key,/^[a-f0-9-]{36}$/);
 assert.equal(items.find(x=>x.item_id==='activation').stance,'HELD');
 assert(d.getElementById('work-brief').textContent.includes('Done, superseded or withdrawn'));
 d.getElementById('work-history-open').click();await waitFor(()=>d.querySelector('#work-history-content details'));
 assert.deepEqual(errors,[]);dom.window.close();
 console.log('WORK DOM acceptance passed: holds/deferrals, update, exact retry after lost response, preserved hold and history.');
})().catch(e=>{console.error(e);process.exitCode=1});
