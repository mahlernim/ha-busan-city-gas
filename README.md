<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/dark_icon.png">
    <img src="brand/icon.png" alt="SK E&S 도시가스" width="88" height="88">
  </picture>
</p>

<h1 align="center">SK E&S 도시가스 · Home Assistant</h1>

<p align="center">가스요금 조회부터 실시간 검침 추정, 보정과 자가검침 제출까지</p>

<p align="center">
  <a href="https://github.com/mahlernim/ha-busan-city-gas/releases/latest"><img src="https://img.shields.io/github/v/release/mahlernim/ha-busan-city-gas?style=flat-square&amp;label=version" alt="최신 버전"></a>
  <a href="docs/installation.md"><img src="https://img.shields.io/badge/Home%20Assistant-2026.7.4%2B-18BCF2?style=flat-square&amp;logo=homeassistant&amp;logoColor=white" alt="Home Assistant 2026.7.4 이상"></a>
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=mahlernim&amp;repository=ha-busan-city-gas&amp;category=integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS 사용자 지정 저장소"></a>
  <a href="https://github.com/mahlernim/ha-busan-city-gas/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/mahlernim/ha-busan-city-gas/validate.yml?branch=main&amp;style=flat-square&amp;label=checks" alt="자동 검증 상태"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/mahlernim/ha-busan-city-gas?style=flat-square" alt="MIT 라이선스"></a>
</p>

<p align="center"><a href="docs/installation.md">설치하기</a> · <a href="docs/providers.md">지원 공급사</a> · <a href="docs/usage.md">사용법</a> · <a href="docs/troubleshooting.md">문제 해결</a> · <a href="docs/feedback.md">문의·제안</a></p>

지난달 가스요금이 얼마였는지 확인하고, 지금까지 얼마나 썼는지 살펴보고, 자가검침까지 Home Assistant에서 처리하세요.

SK E&S 지역 도시가스 이용자를 위한 **비공식 Home Assistant 통합**입니다. 별도 카드나 대시보드를 만들지 않아도 전용 화면에서 조회와 보정을 할 수 있습니다.

## 지원 공급사

부산도시가스, 코원에너지서비스, 충청에너지서비스, 영남에너지서비스 구미·포항, 전남도시가스, 강원도시가스, 전북에너지서비스를 지원합니다. 설정할 때 청구서에 표시된 공급사와 주택용 유형을 선택하세요. 코원에너지서비스는 예상요금 지역도 선택합니다.

8개 공급사는 같은 SK E&S 포털 구조를 사용합니다. 부산 계정은 실제 조회로 검증했으며 다른 지역은 공개 구조와 합성 응답으로 검증했습니다. 실제 계약 유형이나 포털 응답이 예상과 다르면 조회·예상요금·제출을 추측하지 않고 중단합니다. 자동 제출은 모든 지역에서 기본적으로 꺼져 있습니다.

## 무엇을 입력하나요?

처음에는 **선택한 도시가스 공급사 홈페이지 회원 아이디와 비밀번호**만 있으면 됩니다. 고객번호나 계량기 번호를 직접 찾을 필요는 없습니다. 연결된 계약을 불러오며, 여러 개라면 사용할 계약을 선택합니다.

추가 기능은 필요한 것만 설정하세요.

| 준비하거나 입력할 것 | 사용할 수 있는 기능 |
| --- | --- |
| 회원 아이디·비밀번호 | 최근·과거 고지서의 요금과 사용량, 납기일, 자가검침 접수 기간·상태 조회 |
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

1. **요금 확인:** 사이드바의 SK E&S 도시가스 화면에서 고지금액과 예상액을 봅니다. 별도 대시보드 편집은 필요 없습니다.
2. **검침 보정:** 실제 계량기 숫자와 같으면 `맞음`, 조금 다르면 `+0.1 / −0.1`, 차이가 크면 숫자를 입력하고 **보정만 적용**을 누릅니다. 보정은 선택한 도시가스 공급사에 제출하는 동작이 아닙니다.
3. **자가검침 제출:** 접수 기간에 **제출값 확인**을 누르고 계약과 정수값을 확인한 뒤 승인합니다. 예를 들어 128.6 m³라면 128 m³를 보냅니다. **보정하고 제출**도 보정 후 별도 확인을 거칩니다.

주간 보정 알림을 켜면 기본 토요일 오전 10시에 안내하며 시간은 변경할 수 있습니다. 제출 알림을 켜면 접수 기간에 제출 여부를 묻습니다. **마감일 자동 제출은 별도 옵션이며 기본 꺼짐**입니다. 켜면 설정 시각에 접수 여부를 확인한 뒤 전송합니다.

요청 응답만으로 성공이라 표시하지 않고 접수값을 다시 확인합니다. 결과가 불명확하면 중복 전송을 막고 확인 방법을 안내합니다. 이미 접수된 값의 수정은 선택한 도시가스 공급사 홈페이지에서 확인하세요.

## 설치하기

**Home Assistant 2026.7.4 이상**이 필요합니다. 로그인에는 간편조회용 정보가 아닌 홈페이지 회원 아이디·비밀번호를 사용합니다.

[![HACS에서 열기](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mahlernim&repository=ha-busan-city-gas&category=integration)

HACS가 설정되어 있다면 위 버튼을 누르세요. 처음에는 사용자 지정 저장소로 추가해야 할 수 있습니다. HACS 기본 목록에 등록된 통합은 아닙니다.

1. HACS에 이 저장소를 추가하고 **SK E&S 도시가스**를 다운로드합니다.
2. Home Assistant를 재시작합니다.
3. **설정 → 기기 및 서비스 → 통합 추가 → SK E&S 도시가스**에서 로그인합니다.

[처음 설치하는 방법](docs/installation.md) · [검침 보정과 알림 사용법](docs/usage.md) · [문제 해결](docs/troubleshooting.md)

## 도움이 필요하신가요?

설치나 사용 중 궁금한 점은 문제 해결 안내를 확인하세요. 해결되지 않는 문제나 개선 의견은 GitHub에 남길 수 있습니다.

[문의·오류 제보 안내](docs/feedback.md) · [문의 남기기](https://github.com/mahlernim/ha-busan-city-gas/issues/new?template=test-report.yml)

**아이디·비밀번호·계약번호·주소·원본 고지서는 공개 게시물에 올리지 마세요.** 화면 사진을 첨부할 때도 개인정보를 가려주세요.

## 알아두세요

- SK E&S 또는 각 지역 도시가스 공급사가 제공하거나 운영하는 공식 앱이 아닙니다. 홈페이지 변경에 따라 조회가 중단될 수 있습니다.
- 예상 사용량·요금은 참고용입니다. 결제나 가스밸브 제어 기능은 없습니다.
- 알림과 자동 제출은 별도 선택 사항입니다. [제출·자동 제출 사용법](docs/usage.md)을 확인한 뒤 설정하세요.
- 기존 가스센서와 에너지 통계는 그대로 유지합니다.
- [개인정보·권한 안내](docs/privacy.md) · [업데이트 내용](CHANGELOG.md) · [라이선스](LICENSE)

코드로 기여하고 싶다면 [개발 참여 안내](CONTRIBUTING.md)를 참고해 주세요.
