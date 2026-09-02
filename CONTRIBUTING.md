# 개발 참여 안내

일반 사용 후기는 [테스트 참여 안내](docs/testing.md)를 참고해 주세요.

## 로컬 검증

Python 3.14와 Node.js 22 이상을 사용합니다.

```sh
python -m venv .venv
# 가상환경을 활성화한 뒤
pip install -r requirements_test.txt
python -m pytest -p pytest_asyncio.plugin
python -m ruff check custom_components tests tools
python -m ruff format --check custom_components tests tools
node --test tests/test_panel_messages.cjs
node --check custom_components/busan_city_gas/frontend/panel.js
python tools/build.py
```

다른 pytest 플러그인이 설치되어 있다면 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`을 설정하세요.
테스트는 합성 응답과 로컬 모의 HTTP 서버를 사용합니다. CI에 실제 계정이나 실제 제출 테스트를 추가하지 마세요.
`tests/preview.html`은 전용 패널에 예시 데이터를 공급하는 화면입니다. 실행 중인 HA에 연결하지 않습니다.

## 변경 원칙

- 확정 고지서·실측·추정값을 구분하고, 보정을 사용량으로 합산하지 않습니다.
- 제출 성공은 단순 응답이 아니라 같은 주기·같은 값의 접수 확인을 기준으로 판단합니다.
- 응답 유실이나 모호한 결과에 쓰기 요청을 자동 재시도하지 않습니다.
- 실제 제출 잠금은 모의 시험 통과만으로 해제하지 않습니다.
- 안내 문구는 한국어로 작성하고 개발 용어보다 이용자가 할 일을 설명합니다.
- 비밀번호·쿠키·계약 식별자와 개인 운영 기록을 커밋하지 않습니다.

라이선스는 [MIT](LICENSE)입니다. 아이콘의 외부 구성 요소는 [고지](brand/NOTICE.txt)를 유지해 주세요.
