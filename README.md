<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/dark_icon.png">
    <img src="brand/icon.png" alt="똑똑 자가검침 AI" width="88" height="88">
  </picture>
</p>

<h1 align="center">똑똑 자가검침 AI · Home Assistant</h1>

<p align="center">가스요금 조회부터 실시간 검침 추정, 보정과 자가검침 제출까지</p>

<p align="center">
  <a href="https://github.com/mahlernim/ha-busan-city-gas/releases/latest"><img src="https://img.shields.io/github/v/release/mahlernim/ha-busan-city-gas?style=flat-square&amp;label=version" alt="최신 버전"></a>
  <a href="docs/installation.md"><img src="https://img.shields.io/badge/Home%20Assistant-2026.7.4%2B-18BCF2?style=flat-square&amp;logo=homeassistant&amp;logoColor=white" alt="Home Assistant 2026.7.4 이상"></a>
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=mahlernim&amp;repository=ha-busan-city-gas&amp;category=integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS 사용자 지정 저장소"></a>
  <a href="https://github.com/mahlernim/ha-busan-city-gas/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/mahlernim/ha-busan-city-gas/validate.yml?branch=main&amp;style=flat-square&amp;label=checks" alt="자동 검증 상태"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/mahlernim/ha-busan-city-gas?style=flat-square" alt="MIT 라이선스"></a>
</p>

<p align="center"><a href="docs/installation.md">설치하기</a> · <a href="docs/providers.md">지원 공급사</a> · <a href="docs/usage.md">사용법</a> · <a href="docs/troubleshooting.md">문제 해결</a> · <a href="docs/feedback.md">문의·제안</a></p>

<p align="center"><a href="#지원-공급사">한국어</a> · <a href="#english">English</a></p>

지난달 가스요금이 얼마였는지 확인하고, 지금까지 얼마나 썼는지 살펴보고, 자가검침까지 Home Assistant에서 처리하세요.

**똑똑 자가검침 AI**는 가스요금 조회, 검침값 추정과 보정, 자가검침 제출을 함께 처리하는 비공식 Home Assistant 통합입니다. 별도 대시보드 없이 전용 화면에서 사용하세요.

## 지원 공급사

SK E&S 8개 지역, 가스앱 14개 브랜드, 삼천리, 에너지톡, 대성에너지·대성청정에너지, 해양에너지를 연결할 수 있습니다. 가스앱에는 서울도시가스·예스코·인천도시가스·경동도시가스 등이 포함됩니다. [공급사별 기능](docs/providers.md)

이 브랜치는 **37개 공급사·연결 채널 항목**을 제공합니다. v0.6.2의 30개 항목에 귀뚜라미·미래엔서해·참빛 계열의 에너지톡 연결 7개를 추가한 것으로, 새 공급사 7곳을 뜻하지 않습니다. 에너지톡은 참빛의 지역별 서비스를 포함해 11개 연결을 선택합니다. 추가 연결은 차기 릴리스에 포함될 예정이며 현재 HACS 배포본에는 아직 없습니다.

SK E&S 외 새 공급사 연결은 **실험적 지원**이며 실제 사용자 계정에서 테스트할 수 있도록 조회와 제출 경로를 제공합니다. 공개 프로토콜과 합성 응답으로 개발했으므로 계약별 차이가 있을 수 있습니다. 자동 제출은 기본 꺼짐이며, 처음에는 직접 제출 후 공급사에서 접수값을 확인하세요. 예상요금 계산은 현재 SK E&S만 지원하고 다른 공급사는 제공되는 확정 요금·사용량을 표시합니다.

명성파워그린은 조사했지만 사용할 수 있는 고객 조회·접수 프로토콜을 찾지 못해 연결 목록에 포함하지 않았습니다. 에너지톡은 실제 마감일을 제공하지 않아 직접 제출과 HA 자동화 액션을 지원하며 내장 마감일 스케줄러는 사용하지 않습니다.

## 무엇을 입력하나요?

**SK E&S·삼천리·대성·해양에너지:** 홈페이지 회원 아이디·비밀번호. **가스앱:** 본인 명의 휴대폰 정보와 문자 인증번호. **에너지톡:** 카카오 로그인 후 선택한 주소의 세션 토큰. 연결된 계약을 불러오며, 여러 개라면 사용할 계약을 선택합니다. 가스앱의 자가검침 서비스 가입이나 접수 채널 변경이 필요한 경우 전용 화면에서 별도로 확인하고 진행합니다.

추가 기능은 필요한 것만 설정하세요.

| 준비하거나 입력할 것 | 사용할 수 있는 기능 |
| --- | --- |
| 공급사 로그인·가스앱 문자 인증·에너지톡 세션 토큰 | 공급사가 제공하는 고지서 요금·사용량, 납기일과 자가검침 상태 조회 |
| 계량기에 실제로 보이는 숫자 | 검침 기록·보정, 접수 기간에 확인한 숫자 제출 |
| HA의 누적 가스 사용량 센서 + 현재 계량기 숫자 | 센서 증가량을 더한 현재 검침값 추정, 사용 추이에 따른 예상 사용량·요금 |
| Android 휴대폰의 HA Companion App | 주간 검침 보정 알림, 제출할지 묻는 알림과 바로가기 |

**가스센서 없이도 사용할 수 있습니다.** 계량기 기준값과 전년도 사용량으로 현재 검침값을 추정하고, 주기적으로 실제 숫자를 확인하면 최근 사용 추세도 반영합니다. 기준이나 자료가 부족하면 입력을 안내하며 숫자를 임의로 만들지 않습니다. 센서와 휴대폰 알림은 나중에 연결해도 됩니다. [하드웨어 없이 사용하는 방법](docs/estimation.md)

## 어떤 정보를 볼 수 있나요?

- **확정된 요금:** 최근 고지금액, 지난 고지서와 사용량, 납기일을 확인합니다.
- **현재 검침값:** 센서가 있으면 실측 기준에 센서 증가량을 더하고, 없으면 과거 사용량과 유효한 실측 기록으로 추정합니다. 값의 출처를 표시하며 실제 숫자와 차이가 나면 보정할 수 있습니다.
- **이번 청구기간 예상:** 현재까지 사용량·요금과 청구기간 끝까지의 예상 사용량·요금을 확인합니다. 추정의 기준과 자료 부족 여부도 표시합니다.
- **자가검침 상태:** 언제 입력할 수 있는지, 어떤 값이 접수되었는지 확인합니다.

계량기 **검침값은 누적된 숫자**이고, **사용량은 기간 동안 늘어난 양**입니다. 홈페이지에서 받아온 고지금액은 확정값이지만, 예상 요금은 사용 추이·요율로 계산한 참고값이며 실제 청구액과 다를 수 있습니다.

## 평소에는 이렇게 사용하세요

1. **요금 확인:** 사이드바의 똑똑 자가검침 AI 화면에서 고지금액과 예상액을 봅니다. 별도 대시보드 편집은 필요 없습니다.
2. **검침 보정:** 실제 계량기 숫자와 같으면 `맞음`, 조금 다르면 `+0.1 / −0.1`, 차이가 크면 숫자를 입력하고 **보정만 적용**을 누릅니다. 보정은 선택한 도시가스 공급사에 제출하는 동작이 아닙니다.
3. **자가검침 제출:** 접수 기간에 **제출값 확인**을 누르고 계약과 정수값을 확인한 뒤 승인합니다. 예를 들어 128.6 m³라면 128 m³를 보냅니다. **보정하고 제출**도 보정 후 별도 확인을 거칩니다.

주간 보정 알림을 켜면 기본 토요일 오전 10시에 안내하며 시간은 변경할 수 있습니다. 제출 알림을 켜면 접수 기간에 제출 여부를 묻습니다. **마감일 자동 제출은 별도 옵션이며 기본 꺼짐**입니다. 켜면 설정 시각에 접수 여부를 확인한 뒤 전송합니다.

요청 응답만으로 성공이라 표시하지 않고 접수값을 다시 확인합니다. 결과가 불명확하면 중복 전송을 막고 확인 방법을 안내합니다. 이미 접수된 값의 수정은 선택한 도시가스 공급사 홈페이지에서 확인하세요.

## 설치하기

**Home Assistant 2026.7.4 이상**이 필요합니다. 공급사에 따라 홈페이지 로그인, 가스앱 문자 인증 또는 에너지톡 세션 토큰을 사용합니다.

[![HACS에서 열기](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mahlernim&repository=ha-busan-city-gas&category=integration)

HACS가 설정되어 있다면 위 버튼을 누르세요. 처음에는 사용자 지정 저장소로 추가해야 할 수 있습니다. HACS 기본 목록에 등록된 통합은 아닙니다.

1. HACS에 이 저장소를 추가하고 **똑똑 자가검침 AI**를 다운로드합니다.
2. Home Assistant를 재시작합니다.
3. **설정 → 기기 및 서비스 → 통합 추가 → 똑똑 자가검침 AI**에서 로그인합니다.

[처음 설치하는 방법](docs/installation.md) · [검침 보정과 알림 사용법](docs/usage.md) · [문제 해결](docs/troubleshooting.md)

## 도움이 필요하신가요?

설치나 사용 중 궁금한 점은 문제 해결 안내를 확인하세요. 해결되지 않는 문제나 개선 의견은 GitHub에 남길 수 있습니다.

[문의·오류 제보 안내](docs/feedback.md) · [문의 남기기](https://github.com/mahlernim/ha-busan-city-gas/issues/new?template=test-report.yml)

**아이디·비밀번호·세션 토큰·계약번호·주소·원본 고지서는 공개 게시물에 올리지 마세요.** 화면 사진을 첨부할 때도 개인정보를 가려주세요.

## 알아두세요

- 가스앱, SK E&S 또는 각 지역 도시가스 공급사가 제공하거나 운영하는 공식 앱이 아닙니다. 홈페이지 변경에 따라 조회가 중단될 수 있습니다.
- 예상 사용량·요금은 참고용입니다. 결제나 가스밸브 제어 기능은 없습니다.
- 알림과 자동 제출은 별도 선택 사항입니다. [제출·자동 제출 사용법](docs/usage.md)을 확인한 뒤 설정하세요.
- 기존 가스센서와 에너지 통계는 그대로 유지합니다.
- [개인정보·권한 안내](docs/privacy.md) · [업데이트 내용](CHANGELOG.md) · [라이선스](LICENSE)

코드로 기여하고 싶다면 [개발 참여 안내](CONTRIBUTING.md)를 참고해 주세요.

## English

**똑똑 자가검침 AI** is an unofficial Home Assistant integration for Korean city-gas billing, meter estimates, physical calibration, and self-reading submission. It supports eight SK E&S regions, 14 Gasapp brands, Samchully, EnergyTalk, Daesung Energy, Daesung Clean Energy, and Haeyang Energy. Connections outside SK E&S are experimental and have not been verified with every supplier's customer accounts.

This branch offers 37 supplier and connection-channel choices. Seven additional EnergyTalk choices cover Kiturami, Mirae N Seohae, and five Chambit services already represented through Gasapp. They are alternate connections, not seven new suppliers, and are not yet available in the v0.6.2 HACS release. Existing Gasapp entries keep their current connection and history.

### Installation and first use

Home Assistant 2026.7.4 or later and HACS are required for HACS installation. Add this repository as a custom integration repository, download the integration, and restart Home Assistant. Then open **Settings → Devices & services → Add integration → 똑똑 자가검침 AI**. This repository is not in the default HACS catalog.

Choose the supplier shown on your bill. Depending on the supplier, connect with website credentials, Gasapp SMS verification, or an existing EnergyTalk session token. EnergyTalk users sign in on the official site, select their address, and copy only the bearer token from an `/api/fetch` request into the masked token field. Renew the connection through HA reauthentication when it expires. Do not share the token in issues or screenshots.

Select your contract, optionally connect a raw cumulative gas sensor in m³, and enter the physical meter reading. A sensor is optional. Available official history can support estimation, but missing billing periods, heat factors, or tariffs limit usage and cost forecasts.

### Submission and updates

Manual submission requires confirmation. Automatic submission is opt-in and starts disabled. EnergyTalk exposes current permission rather than a future deadline, so it has no built-in last-day submission schedule. Acknowledgements are checked against the supplier's recorded reading, and uncertain outcomes are not automatically retried.

Install future updates through HACS and restart Home Assistant. Existing entries do not need to be deleted or recreated. The domain remains `busan_city_gas`. Estimates are informational, and the integration does not make payments or control gas valves. For support, open an issue with the supplier, failing step, and redacted error message. See the [installation guide](docs/installation.md), [supplier details](docs/providers.md), and [privacy information](docs/privacy.md).
