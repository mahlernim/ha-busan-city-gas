const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const path = require('node:path');
const context = {HTMLElement: class {}, customElements: {define(){}, get(){return undefined;} }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../custom_components/busan_city_gas/frontend/panel.js'),'utf8')+'\nthis.windowMessage=windowMessage;this.errorMessage=errorMessage;',context);
vm.runInContext('this.receiptMessage=receiptMessage;this.Panel=BusanCityGasPanel;', context);
vm.runInContext('this.modelMessage=modelMessage;this.modelLabel=modelLabel;', context);
test('estimation messages distinguish waiting, missing anchor and learning',()=>{
 assert.match(context.modelMessage({source_waiting:true}),/센서 연결 대기/);
 assert.match(context.modelMessage({estimation_status:'anchor_required'}),/실제 숫자/);
 assert.match(context.modelMessage({estimation_status:'insufficient_data'}),/자료가 부족/);
 assert.match(context.modelLabel({estimation_method:'historical_blend'}),/실측 추세/);
 assert.match(context.modelLabel({estimation_method:'recent_physical'}),/최근 실측/);
});
const base = {window_start:'2026-09-13',window_end:'2026-09-18',today:'2026-09-02',submission_status:'not_submitted'};
function renderRow(updates, admin = false) {
 const panel = Object.create(context.Panel.prototype);
 panel.rows = [{...base,key:'synthetic',entry_id:'test',notification:{},bills:[],history:[],...updates}];
 panel._hass = {user:{is_admin:admin}};
 panel.shadowRoot = {innerHTML:''};
 panel.render();
 return panel.shadowRoot.innerHTML;
}
test('partial history is visible without exposing raw adapter errors',()=>{
 const html = renderRow({history_errors:['provider_schema_changed']});
 assert.match(html,/일부 고지서 상세·검침 이력을 불러오지 못했습니다/);
 assert.doesNotMatch(html,/provider_schema_changed/);
 assert.doesNotMatch(renderRow({history_errors:[]}),/일부 고지서 상세·검침 이력/);
});
for (const [flag, action] of [['service_registration_required','register'],['channel_change_required','channel']]) {
 test(`${action} is explained to recipients but actionable only by admins`,()=>{
  const row = {provider_family:'gasapp',[flag]:true};
  assert.match(renderRow(row,true),new RegExp(`data-action="${action}"`));
  const html = renderRow(row,false);
  assert.doesNotMatch(html,new RegExp(`data-action="${action}"`));
  assert.match(html,/관리자에게/);
 });
}
test('before window states exact available dates',()=>{
 const text=context.windowMessage({...base,window_status:'before'});
 assert.match(text,/오늘은.*제출 기간이 아닙니다/);
 assert.match(text,/9월 13일부터 9월 18일까지/);
});
test('expired, unknown and ineligible are distinct',()=>{
 assert.match(context.windowMessage({...base,window_status:'ended'}),/끝났습니다/);
 assert.match(context.windowMessage({...base,window_status:'unknown'}),/확인하지 못했습니다/);
 assert.match(context.windowMessage({...base,window_status:'ineligible'}),/가능한 상태가 아닙니다/);
});
test('confirmed versus uncertain does not falsely claim failure',()=>{
 assert.match(context.windowMessage({...base,submission_status:'confirmed',accepted:'35'}),/다시 제출할 필요가 없습니다/);
 for(const confirmation_source of ['provider_response','optimistic']) {
  const completed=context.windowMessage({...base,submission_status:'confirmed',accepted:'35',confirmation_source});
  assert.match(completed,/공급사 응답을 기준으로.*제출 완료/);
  assert.match(completed,/당월 접수값.*확인할 수 없습니다/);
 }
 assert.match(context.windowMessage({...base,submission_status:'confirmed',accepted:'35',local_reading:'36.8',window_open:true,revision_submission_available:true}),/36 m³로 수정 제출/);
 const text=context.windowMessage({...base,submission_status:'uncertain'});
 assert.match(text,/성공 또는 실패가 확정되지/);
 assert.match(text,/다시 전송하지 않습니다/);
});
test('server closed response overrides stale open screen',()=>{
 assert.match(context.errorMessage({code:'window_closed'},{...base,window_status:'open'}),/전송하지 않았습니다/);
});
test('auth, stale value, lower reading, revision and uncertain have actionable errors',()=>{
 for(const code of ['reauth_required','stale_proposal','below_official_reading','submission_uncertain','submission_disabled','revision_submission_unsupported','revision_submission_unavailable','revision_state_changed','submission_value_unchanged']) {
  const text=context.errorMessage({code},base);
  assert.ok(text.length>20);
  assert.ok(!text.includes(code));
 }
});
test('wire errors are Korean and distinguish mismatch, rejection and unknown receipt',()=>{
 for(const code of ['submission_rejected','submission_attempted_today','submission_value_mismatch','submission_state_unknown','portal_reading_present','submission_contract_changed','submission_metadata_missing']) {
  const text=context.errorMessage({code},base);
  assert.ok(text.length>30);
  assert.ok(!text.includes(code));
 }
 assert.match(context.windowMessage({...base,submission_status:'uncertain',submission_error:'submission_value_mismatch'}),/요청한 값과 다릅니다/);
 assert.match(context.windowMessage({...base,submission_status:'rejected',submission_error:'submission_rejected'}),/원인은 제공되지/);
});

test('contract parsing failures explain that no reading was sent',()=>{
 const row={...base,submission_status:'not_sent',submission_error:'contract_schema_changed'};
 for(const text of [context.errorMessage({code:'contract_schema_changed'},row),context.windowMessage(row),context.receiptMessage(row)]) {
  assert.match(text,/계약 정보/);
  assert.match(text,/전송하지 않았습니다/);
 }
 assert.match(renderRow(row),/계약 정보/);
});

test('unmapped provider preflight failures remain visible as not sent',()=>{
 const row={...base,submission_status:'not_sent',submission_error:'provider_meter_http_503'};
 for(const text of [context.windowMessage(row),context.receiptMessage(row)]) {
  assert.match(text,/전송하지 않았습니다/);
  assert.doesNotMatch(text,/provider_meter_http_503|접수 여부가 불명확/);
 }
 assert.match(context.windowMessage({...row,submission_status:'uncertain'}),/결과를 확인하지 못했습니다/);
});
test('receipt-only results distinguish missing, confirmed and disappearing records',()=>{
 assert.match(context.receiptMessage({...base}),/전송하거나 재전송하지 않았습니다/);
 assert.match(context.receiptMessage({...base,submission_status:'confirmed',accepted:'35',receipt_in_latest_read:true}),/접수 확인: 35/);
 assert.match(context.receiptMessage({...base,submission_status:'confirmed',accepted:'35',receipt_in_latest_read:false}),/이번 조회에는 접수값이 없어/);
 assert.match(context.receiptMessage({...base,submission_status:'confirmed',accepted:'35',confirmation_source:'provider_response',receipt_in_latest_read:false}),/공급사 응답을 기준으로 제출 완료 기록/);
 assert.match(context.receiptMessage({...base,submission_status:'confirmed',accepted:'35',confirmation_source:'optimistic',receipt_in_latest_read:false}),/당월 접수값.*확인할 수 없습니다/);
 assert.match(context.receiptMessage({...base,submission_observed:'35'}),/덮어쓰지 않았습니다/);
});
test('receipt check action never requests a proposal, calibration or submission',async()=>{
 const panel=Object.create(context.Panel.prototype);
 panel.rows=[{...base,key:'synthetic',entry_id:'test'}];
 panel.render=()=>{};
 panel.load=async()=>{};
 const calls=[];
 panel.call=async action=>{calls.push(action);return {...base};};
 await panel.act('check');
 assert.deepEqual(calls,['check_submission']);
 assert.match(panel.message,/재전송하지 않았습니다/);
 assert.equal(panel.busy,false);
});

test('cancelled confirmation never submits and leaves a clear message',async()=>{
 const panel=Object.create(context.Panel.prototype);
 panel.rows=[{key:'a',entry_id:'e',label:'예시 계약'}]; panel.render=()=>{};
 const calls=[];
 panel.call=async action=>{calls.push(action);return {id:'p',value:128,origin:'sensor'};};
 context.window={confirm:()=>false};
 await panel.submit();
 assert.deepEqual(calls,['proposal']);
 assert.match(panel.message,/취소.*전송하지 않았습니다/);
});

test('approved submission uses displayed integer, proposal and original contract',async()=>{
 const panel=Object.create(context.Panel.prototype);
 const row={key:'a',entry_id:'e',label:'예시 계약'};
 panel.rows=[row]; panel.render=()=>{};
 const calls=[];
 panel.call=async(action,data,target)=>{
  calls.push({action,data,target});
  if(action==='proposal') {panel.rows=[{key:'b'}];return {id:'p',value:128,origin:'historical'};}
  return {accepted:'128'};
 };
 context.window={confirm:text=>{assert.match(text,/예시 계약/);assert.match(text,/128 m³/);assert.match(text,/과거 사용량·실측 기록/);return true;}};
 await panel.submit();
 assert.equal(calls.length,2);
 assert.equal(calls[1].data.proposal_id,'p');
 assert.equal(calls[1].target,row);
 assert.match(panel.message,/접수 확인: 128/);
});

test('calibration and submission are separate actions',()=>{
 const html=renderRow({window_open:true,local_reading:'35.4',supports_submission:true,submission_blocked:false});
 assert.match(html,/data-action="apply"[^>]*>보정하기<\/button>/);
 assert.doesNotMatch(html,/apply-submit|보정하고 제출|보정만 적용/);
 assert.match(html,/data-action="submit"[^>]*>현재 값 제출<\/button>/);
});

test('confirmed readings expose only an eligible changed revision',()=>{
 const changed=renderRow({submission_status:'confirmed',accepted:'42',local_reading:'44.2',window_open:true,supports_submission:true,supports_revision_submission:true,revision_submission_available:true,submission_blocked:true});
 const changedButton=changed.match(/<button data-action="submit"[^>]*>수정 제출<\/button>/)?.[0] || '';
 assert.ok(changedButton);
 assert.doesNotMatch(changedButton,/disabled/);

 const same=renderRow({submission_status:'confirmed',accepted:'42',local_reading:'42.9',window_open:true,supports_submission:true,supports_revision_submission:true,revision_submission_available:true,submission_blocked:true});
 const sameButton=same.match(/<button data-action="submit"[^>]*>이미 같은 값으로 제출됨<\/button>/)?.[0] || '';
 assert.match(sameButton,/disabled/);
});

for (const status of ['pending','uncertain']) {
 test(`${status} remains blocked from submission`,()=>{
  const html=renderRow({submission_status:status,local_reading:'44.2',window_open:true,supports_submission:true,submission_blocked:false});
  const submitButton=html.match(/<button data-action="submit"[^>]*>현재 값 제출<\/button>/)?.[0] || '';
  assert.match(submitButton,/disabled/);
 });
}

test('revision confirmation names the prior receipt and replacement value',async()=>{
 const panel=Object.create(context.Panel.prototype);
 const row={key:'a',entry_id:'e',label:'예시 계약',provider_name:'부산도시가스',submission_status:'confirmed',accepted:'41',submission_attempted_at:'2026-09-15 21:00'};
 panel.rows=[row]; panel.render=()=>{};
 const calls=[];
 panel.call=async(action,data,target)=>{
  calls.push({action,data,target});
  if(action==='proposal') return {id:'revision-proposal',value:44,origin:'sensor',revision:true,revision_from:'42',revision_from_at:'2026-09-15 22:13',revision_from_at_kind:'submitted'};
  return {accepted:'44'};
 };
 context.window={confirm:text=>{
  assert.match(text,/2026-09-15 22:13에 42 m³를 이미 제출/);
  assert.match(text,/44 m³로 수정 제출하시겠습니까/);
  assert.match(text,/기존 42 m³ 접수값을 44 m³로 바꾸어/);
  return true;
 }};
 await panel.submit();
 assert.equal(calls[0].action,'proposal');
 assert.equal(calls[0].data.revision,true);
 assert.equal(calls[1].data.proposal_id,'revision-proposal');
 assert.equal(calls[1].target,row);
 assert.match(panel.message,/수정 접수 확인: 42 → 44/);
});

test('revision confirmation distinguishes a provider confirmation timestamp',async()=>{
 const panel=Object.create(context.Panel.prototype);
 panel.rows=[{key:'a',entry_id:'e',submission_status:'confirmed',accepted:'41'}]; panel.render=()=>{};
 panel.call=async action=>action==='proposal'
  ? {id:'p',value:44,revision:true,revision_from:'42',revision_from_at:'2026-09-15 22:20',revision_from_at_kind:'confirmed'}
  : {accepted:'44'};
 context.window={confirm:text=>{
  assert.match(text,/42 m³가 이미 접수/);
  assert.match(text,/2026-09-15 22:20에 접수 내역을 확인/);
  assert.doesNotMatch(text,/2026-09-15 22:20에 42 m³를 이미 제출/);
  return true;
 }};
 await panel.submit();
});

test('revision confirmation permits an unknown submission timestamp',async()=>{
 const panel=Object.create(context.Panel.prototype);
 panel.rows=[{key:'a',entry_id:'e',submission_status:'confirmed',accepted:'41'}]; panel.render=()=>{};
 panel.call=async action=>action==='proposal'
  ? {id:'p',value:44,revision:true,revision_from:'42'}
  : {accepted:'44'};
 context.window={confirm:text=>{
  assert.match(text,/42 m³가 이미 접수/);
  assert.match(text,/제출 시각은 확인되지 않았습니다/);
  return true;
 }};
 await panel.submit();
});

test('provider response completion does not promise current-month readback',async()=>{
 const panel=Object.create(context.Panel.prototype);
 panel.rows=[{key:'a',entry_id:'e',submission_status:'not_submitted'}]; panel.render=()=>{};
 panel.call=async action=>action==='proposal'
  ? {id:'p',value:44,origin:'sensor'}
  : {accepted:'44',confirmation_source:'provider_response',acknowledgement_matcher:'result_y_n'};
 context.window={confirm:()=>true};
 await panel.submit();
 assert.match(panel.message,/공급사 응답 기준 완료/);
 assert.match(panel.message,/당월 접수값.*확인할 수 없습니다/);
 assert.doesNotMatch(panel.message,/재조회.*확인/);
});

test('revision never falls back to stale panel receipt metadata',async()=>{
 const panel=Object.create(context.Panel.prototype);
 panel.rows=[{key:'a',entry_id:'e',submission_status:'confirmed',accepted:'41',submission_attempted_at:'stale time'}]; panel.render=()=>{};
 const calls=[];
 panel.call=async action=>{
  calls.push(action);
  return {id:'p',value:44,revision:true};
 };
 context.window={confirm:()=>{throw new Error('confirmation must not open');}};
 await assert.rejects(panel.submit(),error=>error.code==='submission_metadata_missing');
 assert.deepEqual(calls,['proposal']);
});
for (const action of ['register','channel']) {
 test(`cancelled ${action} consent never calls backend`,async()=>{
  const panel=Object.create(context.Panel.prototype);
  panel.rows=[{key:'a',entry_id:'e',label:'예시 계약'}];
  panel.render=()=>{};panel.load=async()=>{};
  let calls=0; panel.call=async()=>{calls++;};
  context.window={confirm:text=>{assert.match(text,/예시 계약/);assert.match(text,/동의/);return false;}};
  await panel.act(action);
  assert.equal(calls,0);assert.match(panel.message,/취소/);
 });
 test(`approved ${action} sends only consented service preparation`,async()=>{
  const panel=Object.create(context.Panel.prototype);
  const row={key:'a',entry_id:'e',label:'예시 계약'};
  panel.rows=[row];panel.render=()=>{};panel.load=async()=>{};
  const calls=[];panel.call=async(command,data,target)=>calls.push({command,data,target});
  context.window={confirm:text=>{assert.match(text,action==='register'?/서비스에 가입/:/채널을 가스앱으로 변경/);return true;}};
  await panel.act(action);
  assert.equal(calls.length,1);assert.equal(calls[0].command,'prepare_service');
  assert.equal(calls[0].data.action,action);assert.equal(calls[0].data.consent,true);
  assert.equal(calls[0].target,row);assert.match(panel.message,/검침값은 전송하지 않았습니다/);
 });
}

test('dynamic permission does not invent a utility deadline',()=>{
 const text=context.windowMessage({...base,window_start:null,window_end:null,window_status:'open',supports_deadline:false});
 assert.match(text,/현재 자가검침을 허용/);
 assert.match(text,/마감일은 제공되지/);
 assert.ok(!text.includes('미확인부터'));
});
