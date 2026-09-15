## 한국어

로그인 세션이 만료된 뒤 자가검침 제출이 중단되고 화면 반응이 불분명하던 문제를 수정했습니다.

- SK E&S 계열 8개 포털에서 세션 만료를 감지하고 제출 전 조회를 한 번 다시 로그인하여 복구합니다.
- 해양에너지의 조회 세션 복구와 대성 계열의 오류 안내를 개선했습니다. 실제 제출 요청은 자동 재전송하지 않습니다.
- 처리 중·완료·실패 안내를 스크롤 중에도 표시하고, 전송 전 중단과 접수 확인 필요 상태를 구분합니다.

HACS에서 업데이트한 뒤 Home Assistant를 재시작하고 검침 화면을 새로고침하세요. 접수 확인 필요 상태가 남아 있으면 공급사 접수 내역을 먼저 확인하세요. 부산도시가스 외 공급사는 코드와 모의 응답으로 검증했으며 실제 계정 접수는 검증하지 않았습니다.

## English

Fixed submissions stopping after a login session expired without clear feedback in the panel.

- All eight SK E&S portals now detect expired sessions and retry the pre-submission read once after signing in again.
- Improved read-session recovery for Haeyang Energy and actionable pre-submission errors for Daesung providers. Actual submission requests are never replayed automatically.
- Processing, completion, and failure messages stay visible while scrolling. Requests stopped before transmission are distinguished from submissions awaiting receipt confirmation.

Update through HACS, restart Home Assistant, and refresh the meter panel. Check the provider's receipt history first if a submission still needs confirmation. Providers other than Busan City Gas were checked through code review and simulated responses, without real-account submission validation.
