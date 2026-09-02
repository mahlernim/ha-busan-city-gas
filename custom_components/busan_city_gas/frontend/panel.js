/* Bundled, dependency-free HA panel. No credentials or external CDN requests. */
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const number = (value, digits = 1) => value === null || value === undefined ? "—" : Number(value).toLocaleString("ko-KR", {maximumFractionDigits: digits});
const errors = {
  submission_disabled: "현재 제출 기능이 중지되어 있습니다. 부산도시가스 홈페이지에서 직접 제출해 주세요.",
  submission_uncertain: "접수 여부가 불명확합니다. 홈페이지에서 확인해 주세요. 재전송은 중지했습니다.",
  submission_rejected: "서버가 저장 실패로 응답했고 재조회에서도 접수값을 확인하지 못했습니다. 구체적인 원인은 제공되지 않았습니다. 오늘은 다시 보내지 않습니다. 홈페이지에서 확인해 주세요.",
  submission_attempted_today: "오늘 이미 제출을 시도했습니다. 중복 전송을 막기 위해 오늘은 다시 보내지 않습니다. 홈페이지에서 접수 내역을 확인해 주세요.",
  submission_value_mismatch: "조회된 접수값이 요청한 값과 다릅니다. 성공으로 처리하지 않았으며 재전송을 중지했습니다. 홈페이지에서 두 값을 확인해 주세요.",
  submission_state_unknown: "검침 숫자는 조회되지만 접수 상태가 명확하지 않습니다. 성공으로 표시하거나 다시 전송하지 않습니다. 홈페이지에서 확인해 주세요.",
  portal_reading_present: "이미 입력된 것으로 보이는 검침값이 있어 덮어쓰지 않았습니다. 홈페이지에서 접수 상태를 확인해 주세요.",
  submission_contract_changed: "계약 또는 계량기 정보가 달라 전송하지 않았습니다. 공식 정보를 새로고침하고 다시 확인해 주세요.",
  submission_metadata_missing: "제출에 필요한 정보를 확인하지 못해 전송하지 않았습니다. 홈페이지에서 확인하거나 통합 업데이트를 확인해 주세요.",
  invalid_submission_value: "제출값은 0부터 99999 사이의 정수여야 합니다. 계량기 숫자를 확인해 주세요.",
  invalid_submission_time: "제출 시각의 시간대를 확인하지 못해 전송하지 않았습니다. Home Assistant 시간대 설정을 확인해 주세요.",
  stale_proposal: "값이 변경되었거나 요청이 만료되었습니다. 최신 값을 확인해 주세요.",
  invalid_number: "올바른 숫자를 입력해 주세요.",
  below_last_physical: "이전 실측값보다 작습니다. 계량기 교체 여부를 확인해 주세요.",
  physical_calibration_required: "계량기의 실제 숫자로 보정해 주세요.",
  window_closed: "지금은 자가검침 입력 기간이 아닙니다.",
  not_authorized: "이 계약을 처리할 권한이 없습니다.",
  historical_submission_disabled: "과거 사용량 기반 제출이 허용되지 않았습니다.",
  cannot_connect: "부산도시가스 서버에 연결하지 못했습니다. 통신을 확인한 뒤 공식 정보를 새로고침하세요. 제출을 요청한 뒤 응답이 끊겼다면 접수 내역을 먼저 확인하고 다시 누르지 마세요.",
  reauth_required: "로그인이 만료되었습니다. 통합 설정에서 다시 로그인한 뒤 접수 내역을 확인하세요.",
  invalid_auth: "부산도시가스 아이디 또는 비밀번호를 확인하고 다시 로그인하세요.",
  below_official_reading: "입력값이 지난 공식 검침값보다 작아 전송하지 않았습니다. 계량기 숫자와 교체 여부를 확인해 주세요.",
  meter_schema_changed: "부산도시가스의 검침 응답을 해석하지 못했습니다. 기간·접수 상태를 확인할 수 없어 제출하지 않습니다. 홈페이지에서 확인하거나 통합 업데이트를 확인하세요.",
  meter_selection_required: "계약의 계량기를 하나로 확인할 수 없습니다. 홈페이지에서 계량기 정보를 확인해 주세요. 제출하지 않았습니다.",
  submission_transport_not_validated: "실제 제출 연동이 아직 검증되지 않아 전송하지 않았습니다. 홈페이지에서 직접 제출해 주세요.",
  insufficient_data: "제출할 검침값 또는 접수 기간이 아직 확인되지 않았습니다. 공식 정보를 새로고침하고 필요하면 실제 계량기 숫자로 보정하세요.",
  operation_failed: "요청 처리 결과를 확인하지 못했습니다. 공식 정보와 접수 내역을 먼저 확인하세요. 제출을 요청했다면 확인 없이 재전송하지 마세요.",
};

const shortDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? `${Number(value.slice(5,7))}월 ${Number(value.slice(8,10))}일` : "미확인";
const windowMessage = r => {
  const period = `${shortDate(r.window_start)}부터 ${shortDate(r.window_end)}까지`;
  if(r.submission_status === "confirmed") return `이번 주기는 ${number(r.accepted)} m³로 접수가 확인되었습니다. 다시 제출할 필요가 없습니다.`;
  if(r.submission_error && errors[r.submission_error]) return errors[r.submission_error];
  if(["pending","uncertain"].includes(r.submission_status)) return "이전 제출의 접수 여부를 확인해야 합니다. 성공 또는 실패가 확정되지 않았으므로 다시 전송하지 않습니다. 부산도시가스 홈페이지에서 접수 내역을 확인해 주세요.";
  const status = r.window_status || (r.window_open ? "open" : (r.today && r.window_start && r.today < r.window_start ? "before" : "unknown"));
  if(status === "before") return `오늘은 자가검침 제출 기간이 아닙니다. ${period} 제출 가능합니다.`;
  if(status === "ended") return `이번 자가검침 제출 기간(${period})이 끝났습니다. 다음 접수 기간은 공식 조회로 다시 확인해야 합니다.`;
  if(status === "ineligible") return "현재 계약은 온라인 자가검침이 가능한 상태가 아닙니다. 부산도시가스 홈페이지에서 대상 여부를 확인해 주세요.";
  if(status === "open") return `오늘은 자가검침 접수 기간입니다. ${period} 제출 가능합니다.`;
  return "자가검침 제출 기간을 아직 확인하지 못했습니다. 공식 정보를 새로고침해 주세요. 확인 전에는 제출할 수 없습니다.";
};
const receiptMessage = r => {
  if(r.submission_status === "confirmed" && r.receipt_in_latest_read === false) return "이전에 확인된 접수 기록은 유지했습니다. 다만 이번 조회에는 접수값이 없어 홈페이지에서 확인이 필요합니다. 다시 제출하지 않습니다.";
  if(r.submission_status === "confirmed") return `접수 확인: ${number(r.accepted)} m³. 검침값을 새로 전송하지 않았습니다.`;
  if(r.submission_error) return errors[r.submission_error] || errors.submission_uncertain;
  if(r.submission_observed != null) return errors.portal_reading_present;
  return "현재 조회에서 접수 내역을 확인하지 못했습니다. 검침값을 전송하거나 재전송하지 않았습니다.";
};
const errorMessage = (error, row) => error?.code === "window_closed" ? `조회한 접수 기간 또는 주기가 현재 요청과 맞지 않아 전송하지 않았습니다. 마지막으로 확인한 기간은 ${shortDate(row.window_start)}부터 ${shortDate(row.window_end)}까지입니다. 공식 정보를 새로고침해 주세요.` : errors[error?.code] || "처리 결과를 확인하지 못했습니다. 공식 정보와 접수 내역을 먼저 확인해 주세요. 제출을 요청했다면 확인 없이 재전송하지 마세요.";

class BusanCityGasPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this.rows = [];
    this.key = new URLSearchParams(window.location.search).get("contract");
    this.message = "";
    this.busy = false;
    this.loading = false;
    this.draft = "";
    this.shadowRoot.addEventListener("click", e => {
      const button = e.target.closest("button[data-action]");
      if (button && !this.busy) this.act(button.dataset.action);
    });
    this.shadowRoot.addEventListener("input", e => {if (e.target.id === "reading") this.draft = e.target.value;});
    this.shadowRoot.addEventListener("change", e => {
      if(e.target.id === "contract") {this.key = e.target.value; this.draft = ""; this.render();}
    });
  }
  set hass(value) {
    this._hass = value;
    if (!this.started) {
      this.started = true;
      this.load();
      this.subscription = value.connection.subscribeMessage(() => this.load(), {type:"busan_city_gas/subscribe"});
    }
  }
  disconnectedCallback() {
    if(this.subscription) this.subscription.then(unsubscribe => unsubscribe());
    this.started = false;
  }
  get current() {return this.rows.find(r => r.key === this.key) || this.rows[0];}
  async call(action, extra = {}, row = this.current) {
    return this._hass.callWS({type:`busan_city_gas/${action}`, entry_id:row.entry_id, key:row.key, ...extra});
  }
  async load() {
    if(this.loading) return;
    this.loading = true;
    try {this.rows = await this._hass.callWS({type:"busan_city_gas/list"});}
    catch(e) {this.message = errors[e.code] || "정보를 불러오지 못했습니다.";}
    finally {this.loading = false; this.render();}
  }
  async apply(reading, physical, extra = {}) {
    try {
      return await this.call("calibrate", {reading, physical, expected:this.current.local_reading ?? undefined, ...extra});
    } catch(e) {
      if(e.code === "confirm_large_correction" && window.confirm("현재 추정값과 5 m³ 넘게 차이가 납니다. 계량기 숫자를 다시 확인했나요?")) {
        return this.call("calibrate", {reading, physical, accept_large:true, expected:this.current.local_reading ?? undefined, ...extra});
      }
      throw e;
    }
  }
  async submit() {
    const row = this.current;
    const proposal = await this.call("proposal", {}, row);
    const origin = proposal.origin === "historical" ? " (작년 사용량 기반 추정)" : "";
    if(!window.confirm(`${row.label || "선택한 계약"}\n${proposal.value} m³${origin}을 부산도시가스에 제출할까요?\n소수점은 버리고 정수만 전송합니다. 접수된 값의 변경은 홈페이지에서 확인해 주세요.`)) {
      this.message = "제출을 취소했습니다. 검침값은 전송하지 않았습니다. 보정한 값은 유지됩니다.";
      return;
    }
    this.message = "검침값 전송 및 접수 확인 중… 다시 누르지 마세요.";
    this.render();
    const result = await this.call("submit", {proposal_id:proposal.id}, row);
    this.message = `접수 확인: ${result.accepted} m³`;
  }
  async act(action) {
    const row = this.current;
    if(!row) return;
    this.busy = true; this.message = action === "check" ? "접수 상태 조회 중… 검침값은 전송하지 않습니다." : "처리 중…"; this.render();
    try {
      if(action === "refresh") {
        await this._hass.callWS({type:"busan_city_gas/refresh", entry_id:row.entry_id});
        this.message = "조회 상태를 갱신했습니다.";
      } else if(action === "check") {
        this.message = receiptMessage(await this.call("check_submission"));
      } else if(action === "test") {
        const result = await this.call("test_notification");
        this.message = `발송 요청 ${result.requested}대 / 실패 ${result.failed}대. 휴대폰 수신 확인은 별도입니다.`;
      } else if(action === "submit") {
        await this.submit();
      } else {
        let value;
        let physical = true;
        if(action === "plus" || action === "minus") {
          value = (Number(row.local_reading) + (action === "plus" ? 0.1 : -0.1)).toFixed(2);
          physical = false;
        } else if(action === "confirm") {value = number(row.local_reading, 1).replaceAll(",", "");}
        else {
          if(!this.draft.trim()) throw {code:"invalid_number"};
          value = this.draft;
        }
        let extra = {};
        if(action === "replace") {
          if(!window.confirm("물리적 계량기를 교체했나요? 새 계량기 숫자로 기준을 재설정합니다.")) return;
          extra = {replace_meter:true, accept_large:true};
        }
        await this.apply(value, physical, extra);
        this.message = physical ? "실측값을 보정했습니다." : "추정값을 조정했습니다. 실측 확인은 아직 하지 않았습니다.";
        if(action === "apply-submit") {
          this.message = "보정 완료. 제출 확인 중…";
          try {await this.submit();}
          catch(e) {this.message = `보정은 완료되었습니다. 제출: ${errorMessage(e, row)}`;}
        }
      }
    } catch(e) {this.message = errorMessage(e, row);}
    finally {this.busy = false; await this.load();}
  }
  render() {
    const focusedId = this.shadowRoot.activeElement?.id;
    const r = this.current;
    const disabled = this.busy ? "disabled" : "";
    const button = (id, label, off = false) => `<button data-action="${id}" ${disabled} ${off ? "disabled" : ""}>${label}</button>`;
    const fmt = (value, unit = "m³") => `${number(value, unit === "원" ? 0 : 1)} ${unit}`;
    this.shadowRoot.innerHTML = `<style>
      :host{display:block;background:var(--primary-background-color,#f5f7f8);color:var(--primary-text-color,#18302c);min-height:100%;font:16px system-ui,sans-serif}
      main{max-width:840px;margin:auto;padding:24px 18px 72px}h1{font-size:25px;margin:0}h2{font-size:18px;margin:0 0 14px}
      header{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:22px}section{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#dce5e0);border-radius:16px;padding:22px;margin:16px 0}
      .big{font-size:44px;font-weight:700;margin:12px 0}.muted,small{color:var(--secondary-text-color,#61766d)}.warning{background:var(--warning-background-color,color-mix(in srgb,var(--warning-color,#b97b10) 16%,var(--card-background-color,#fff)));color:var(--primary-text-color,#614b10);border-left:3px solid var(--warning-color,#b97b10);padding:14px;border-radius:10px;margin:12px 0}
      .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.metric{font-size:25px;font-weight:600;margin-top:6px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
      button{background:var(--primary-color,#126b54);color:var(--text-primary-color,white);border:0;border-radius:9px;padding:13px 16px;font:inherit;cursor:pointer;min-height:46px}button:disabled{opacity:.45;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--accent-color,#e7ac42);outline-offset:3px}
      input,select{box-sizing:border-box;font:inherit;border:1px solid #98aaa3;border-radius:8px;padding:12px;max-width:100%;background:var(--card-background-color,#fff);color:inherit}input{width:100%;margin-top:8px}label{display:block}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px 4px;border-bottom:1px solid var(--divider-color,#e4eae6)}
      .status{min-height:24px;white-space:pre-wrap}a{color:var(--primary-color,#126b54)}details{margin:15px 0}summary{cursor:pointer}.scroll{overflow:auto}@media(max-width:500px){main{padding:18px 12px 48px}section{padding:18px}.grid{gap:10px}.metric{font-size:21px}header{align-items:start;flex-direction:column}.big{font-size:40px}.actions button{flex:1}}
    </style><main><header><h1>부산도시가스</h1>${this.rows.length > 1 ? `<select id="contract" aria-label="계약 선택">${this.rows.map(row => `<option value="${esc(row.key)}" ${r?.key === row.key ? "selected" : ""}>${esc(row.label)}</option>`).join("")}</select>` : ""}</header>
    <div class="status" role="status" aria-live="polite">${esc(this.message)}</div>
    ${r ? `
      ${r.refreshing ? `<section role="status" aria-live="polite"><strong>공식 정보 조회 중</strong><p>${esc(r.refresh_progress)}</p><small>이미 불러온 검침값과 요금은 사용할 수 있습니다. 조회 완료 시 자동으로 갱신됩니다.</small></section>` : ""}
      <section><small>${esc(r.label)} · ${r.origin === "historical" ? "작년 사용량 기반 추정" : r.source_configured ? "실측 기준 + 센서 증가량" : "수동 검침"}</small>
      <div class="big">${fmt(r.reading)}</div><div class="muted">마지막 실측 ${fmt(r.actual)} · ${esc(r.actual_at || "아직 없음")}</div>
      ${r.gap && r.source_configured ? '<p class="warning">측정 공백 또는 초기화가 있습니다. 자동 제출 전에 실측 보정이 필요합니다.</p>' : ""}
      <div class="actions">${button("confirm","맞음",r.local_reading === null)}${button("plus","+0.1",r.local_reading === null)}${button("minus","−0.1",r.local_reading === null)}</div>
      <label for="reading">현재 계량기에 보이는 숫자 (m³)</label><input id="reading" inputmode="decimal" type="number" step="0.1" min="0" max="99999" value="${esc(this.draft)}" placeholder="예: 35.0">
      <div class="actions">${button("apply","보정만 적용")}${button("apply-submit","보정하고 제출",r.submission_locked || !r.window_open || r.submission_blocked)}</div>
      <small>숫자만 입력하면 적용되지 않습니다. ±0.1은 추정값 조정이며 실측 확인과 구분됩니다.</small>
      <details><summary>계량기를 교체했나요?</summary><p>위에 새 계량기 숫자를 입력한 뒤 기준을 재설정하세요.</p>${button("replace","새 계량기 기준 설정")}</details></section>
      <section><h2>가스요금</h2><div class="grid"><div><small>최근 확정 고지금액</small><div class="metric">${fmt(r.billed_amount,"원")}</div></div><div><small>이번 청구기간 예상액</small><div class="metric">${fmt(r.projected_amount,"원")}</div></div><div><small>현재까지 예상액</small><div class="metric">${fmt(r.accrued_amount,"원")}</div></div><div><small>최근 14일 사용일 평균</small><div class="metric">${number(r.average,2)} m³/일</div></div></div>
      <p>현재까지 사용량 ${fmt(r.usage)} · 기간 전체 예상 ${fmt(r.projected_usage)}</p>
      ${r.due_date ? `<p>최근 고지서 납기일 ${esc(r.due_date)} · 고지 사용량 ${fmt(r.billed_usage)}</p>` : ""}
      <p class="muted">최근 14일 중 ${r.average_days}일 기준 · 사용량 0인 날과 불완전한 날 제외</p><small>예상액은 확정 청구액이 아닙니다. 최신 확인 계수·요율을 사용하며 할인·정산은 다를 수 있습니다.</small>
      ${r.period_start ? `<p>예상 사용기간 ${esc(r.period_start)} ~ ${esc(r.period_end)} (종료 예정)</p>` : ""}</section>
      <section><h2>자가검침</h2><p>입력 기간 ${esc(r.window_start || "확인 중")} ~ ${esc(r.window_end || "확인 중")}</p>
      <p role="status">${esc(windowMessage(r))}</p>
      <p>상태: ${esc(({not_submitted:"미제출",pending:"처리 중",uncertain:"접수 확인 필요",confirmed:"접수 확인됨",rejected:"서버 저장 실패 응답",not_sent:"전송 전 중단"})[r.submission_status] || r.submission_status)}${r.accepted != null ? ` · 조회된 접수값 ${fmt(r.accepted)}` : ""}</p>
      ${r.submission_proposed != null ? `<p>마지막 요청값 ${fmt(r.submission_proposed)} · ${esc(r.submission_attempted_at || "시각 미확인")}</p>` : ""}
      ${r.accepted_checked_at ? `<p class="muted">접수 상태 확인 시각 ${esc(r.accepted_checked_at)} · 이번 조회값 ${r.submission_observed != null ? fmt(r.submission_observed) : "미확인"}</p>` : ""}
      ${r.submission_status === "confirmed" && r.receipt_in_latest_read === false ? `<p class="warning">${esc(receiptMessage(r))}</p>` : ""}
      <div class="actions">${button("check",r.submission_checking ? "접수 확인 중…" : "접수 상태만 확인",r.submission_checking)}</div>
      <small>접수 내역만 조회합니다. 검침값 제출·재전송 및 고지서 재조회는 하지 않습니다.</small>
      ${r.submission_locked ? '<p class="warning">현재 제출 기능이 중지되어 있습니다. 필요한 자가검침은 홈페이지에서 직접 해주세요.</p>' : ""}
      <p>마감일 자동 제출: ${r.automatic_submission ? "켜짐" : "꺼짐"}</p>
      ${r.automatic_submission_needs_confirmation ? '<p class="warning">이전 버전의 자동 제출 설정은 실행하지 않습니다. 사용하려면 통합 설정의 제출 정책에서 다시 켜고 저장해 주세요.</p>' : ""}
      <div class="actions">${button("submit","제출값 확인",r.submission_locked || !r.window_open || r.submission_blocked || ["confirmed","pending","uncertain"].includes(r.submission_status))}${button("test","알림 테스트")}${this._hass?.user?.is_admin ? button("refresh",r.refreshing ? "조회 중…" : "공식 정보 새로고침",r.refreshing) : ""}</div>
      <small>알림 발송 요청 ${r.notification.requested_count || 0}대 · 수신 확인 ${r.notification.received_count || 0}명</small>
      ${r.error ? `<p class="warning">공식 조회가 최신 상태가 아닙니다: ${esc(r.error)}</p>` : ""}<p class="muted">마지막 공식 조회 ${esc(r.last_refresh || "아직 없음")}</p>
      ${r.meter_error ? `<p class="warning">${esc(errorMessage({code:r.meter_error},r))}</p>` : ""}
      ${this._hass?.user?.is_admin ? '<a href="/config/integrations/integration/busan_city_gas">센서·알림·제출 설정 변경</a>' : ""}</section>
      <section><h2>고지서 이력</h2><div class="scroll"><table><thead><tr><th>청구월</th><th>사용량</th><th>고지금액</th></tr></thead><tbody>${r.bills.map(b => `<tr><td>${esc(b.month)}</td><td>${fmt(b.usage)}</td><td>${fmt(b.amount,"원")}</td></tr>`).join("")}</tbody></table></div>
      <details><summary>보정·제출 이력</summary>${r.history.slice(0,30).map(h => `<p>${esc(h.at)} · ${esc(({physical:"실측 보정",adjustment:"추정 조정",submitted:"제출",source_reset:"센서 초기화"})[h.kind] || h.kind)} ${h.value ? fmt(h.value) : ""}</p>`).join("") || '<p class="muted">아직 이력이 없습니다.</p>'}</details></section>
    ` : '<section>표시할 계약이 없습니다. 통합을 설정하거나 담당자 권한을 확인해 주세요.</section>'}</main>`;
    if(focusedId) this.shadowRoot.getElementById(focusedId)?.focus({preventScroll:true});
  }
}
if(!customElements.get("busan-city-gas-panel")) customElements.define("busan-city-gas-panel", BusanCityGasPanel);
