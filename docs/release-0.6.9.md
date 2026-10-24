## 한국어

해양에너지 계정의 요금과 검침 정보를 조회할 때 Home Assistant 이벤트 루프를 차단하지 않도록 수정했습니다.

- 시간대 정보를 조회할 때마다 디스크에서 읽지 않고 한 번만 불러오도록 바꿨습니다. Home Assistant 로그에 나타나던 차단 호출 경고가 더 이상 표시되지 않습니다.
- 설치 안내를 HACS에서 **똑똑 자가검침 AI**를 검색해 설치하는 방법 중심으로 바꿨습니다. 검색 결과에 보이지 않으면 사용자 지정 저장소로 추가할 수 있습니다.
- 통합의 설정, 저장된 데이터 및 공급사 제출 동작은 변경되지 않습니다.

HACS에서 업데이트한 뒤 Home Assistant를 재시작하세요.

## English

Haeyang Energy bill and meter lookups no longer block the Home Assistant event loop.

- The time zone is now loaded once instead of being read from disk on each lookup. The blocking-call warning in the Home Assistant log no longer appears.
- The installation guide now leads with searching for **똑똑 자가검침 AI** in HACS. Adding a custom repository remains available if it does not appear in the search results.
- Configuration, stored data, and provider submission behavior are unchanged.

Update through HACS, then restart Home Assistant.
