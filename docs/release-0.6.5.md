# 똑똑 자가검침 AI v0.6.5 공급사 등록 완료 응답 반영

## 한국어

공급사가 검침값 등록 완료를 명시적으로 응답하면 공식 재조회 반영을 기다리지 않고 제출 완료로 기록합니다. SK E&S, 가스앱, EnergyTalk와 삼천리의 확인된 응답 형식을 공급사별 matcher로 구분하며, 어떤 응답이 사용되었는지도 저장합니다.

직후 공식 재조회가 비어 있거나 일시적으로 실패해도 공급사 완료 응답을 불확실 상태로 낮추거나 같은 값을 다시 보내지 않습니다. 나중에 같은 값이 조회되면 확인 근거를 재조회로 갱신합니다. 필드 누락, 알 수 없는 값, 충돌하는 응답, 단순 HTTP 성공은 완료로 추정하지 않습니다.

HACS에서 업데이트한 뒤 Home Assistant를 재시작하세요. 이미 불확실 상태로 저장된 과거 제출은 자동으로 완료 처리하지 않으므로 공급사 접수 내역을 직접 확인해 주세요.

## English

An explicit provider acknowledgement now completes a meter-reading submission without waiting for the provider's readback view to catch up. Confirmed response formats for SK E&S, Gasapp, EnergyTalk, and Samchully are handled by provider-scoped matchers, and the evidence source is retained.

An empty or temporarily failing readback no longer downgrades an acknowledged submission or allows the same value to be sent again. A later matching readback upgrades the evidence source. Missing, unknown, or conflicting fields—and HTTP success by itself—remain unconfirmed.

Update through HACS and restart Home Assistant. Previously stored uncertain submissions are not upgraded automatically; verify those in the provider's official submission history.
