## 한국어

보정과 공급사 제출을 분리하고, 부산도시가스의 접수 기간 중 수정 제출을 추가했습니다.

- 계량기 실측값은 **보정하기**, 공급사 전송은 **현재 값 제출**에서 별도로 확인합니다.
- 부산도시가스에 이미 접수된 정수값과 현재 제출 예정값이 다르면 **수정 제출**을 사용할 수 있습니다. 확인창에 기존 값·시각과 새 값을 함께 표시합니다.
- 공식 조회에서 다른 접수값이 보이면 수정 제출을 중단합니다. 당월 접수 내역을 조회할 수 없는 부산도시가스는 오류 없는 정상 제출 응답을 공급사 응답 기준 완료로 보존합니다. 응답 유실·불명 상태는 완료로 간주하지 않고 자동 재시도하지 않습니다.
- 자동 제출은 이미 접수된 값을 수정하지 않으며, 수정 제출은 현재 부산도시가스에만 제공합니다.

HACS에서 업데이트한 뒤 Home Assistant를 재시작하고 검침 화면을 새로고침하세요.

## English

Separated local calibration from provider submission and added in-window revision submission for Busan City Gas.

- **Calibrate** only updates the local physical meter anchor. **Submit current value** opens a separate confirmation before transmitting anything.
- When Busan City Gas already has a different integer reading for the open cycle, **Revise submission** shows the prior value and time alongside the new integer value.
- The revision proposal binds the prior accepted value. If an official read exposes a different receipt before transmission, the write stops and asks for fresh confirmation. Because the Busan City Gas portal does not expose the current month's receipt, an error-free Busan response is stored as provider-response completion; lost or ambiguous responses are never retried automatically.
- Automatic submission never revises an existing receipt. Revision submission is currently enabled only for Busan City Gas, where the behavior was validated.

Update through HACS, restart Home Assistant, and refresh the meter panel.
