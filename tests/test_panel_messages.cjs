const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const path = require('node:path');
const context = {HTMLElement: class {}, customElements: {define(){}, get(){return undefined;} }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../custom_components/busan_city_gas/frontend/panel.js'),'utf8')+'\nthis.windowMessage=windowMessage;this.errorMessage=errorMessage;',context);
vm.runInContext('this.receiptMessage=receiptMessage;this.Panel=BusanCityGasPanel;', context);
const base = {window_start:'2026-09-13',window_end:'2026-09-18',today:'2026-09-02',submission_status:'not_submitted'};
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
 const text=context.windowMessage({...base,submission_status:'uncertain'});
 assert.match(text,/성공 또는 실패가 확정되지/);
 assert.match(text,/다시 전송하지 않습니다/);
});
test('server closed response overrides stale open screen',()=>{
 assert.match(context.errorMessage({code:'window_closed'},{...base,window_status:'open'}),/전송하지 않았습니다/);
});
test('auth, stale value, lower reading and uncertain have actionable errors',()=>{
 for(const code of ['reauth_required','stale_proposal','below_official_reading','submission_uncertain','submission_unverified']) {
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
test('receipt-only results distinguish missing, confirmed and disappearing records',()=>{
 assert.match(context.receiptMessage({...base}),/전송하거나 재전송하지 않았습니다/);
 assert.match(context.receiptMessage({...base,submission_status:'confirmed',accepted:'35',receipt_in_latest_read:true}),/접수 확인: 35/);
 assert.match(context.receiptMessage({...base,submission_status:'confirmed',accepted:'35',receipt_in_latest_read:false}),/이번 조회에는 접수값이 없어/);
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
