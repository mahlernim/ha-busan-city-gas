## 한국어

Home Assistant가 관리하는 Beautiful Soup 버전과 충돌하지 않도록 의존성 선언을 수정했습니다.

- `beautifulsoup4` 요구 사항을 정확한 버전 고정(`==4.14.3`)에서 최소 버전(`>=4.14.3`)으로 변경했습니다.
- 통합의 기능과 설정, 저장된 데이터 및 공급사 제출 동작은 변경되지 않습니다.

HACS에서 업데이트한 뒤 Home Assistant를 재시작하세요.

## English

Adjusted the dependency declaration so it remains compatible with the Beautiful Soup version managed by Home Assistant.

- Changed the `beautifulsoup4` requirement from an exact pin (`==4.14.3`) to a minimum version (`>=4.14.3`).
- Integration behavior, configuration, stored data, and provider submission behavior are unchanged.

Update through HACS, then restart Home Assistant.
