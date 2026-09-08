# EnergyTalk protocol implementation evidence

Inspected 2026-09-04. The adapter implements the public frontend request contract without authenticated probes. Success payload fixtures are assumptions for users to test, not verified customer responses.

## Authentication and contract scope

Official Kakao authentication returns to `https://energytalk.ai/auth`; that callback cannot be replaced with an HA URL. The practical integration accepts `energytalk_token`, copied from the Authorization bearer header of a request in the user's already signed-in, address-selected official session. The token itself, without the `Bearer ` prefix, belongs in the password-type onboarding field. Never put it in issues or diagnostics. When access expires the user replaces this token through reauthentication.

The browser stores session data under USER_INFO, with some fields encoded. Copying an observed Authorization value avoids depending on its storage encoding. Login and address selection stay in the official site. The integration does not collect Kakao passwords, redeem codes, register addresses, or switch token scope.

`GET /gas/api/user/info` must report the chosen tenant. Registered addresses come from `GET /gas/api/address/list/registered`; the selected address is matched exactly against session `address` and must resolve to one `custNo`. Tokens from a session without a selected address are rejected with `energytalk_address_required`. One entry represents the token's selected contract. Additional addresses require their own selected-session token and entry. Returned `clientId`, `address`, `custNo` and address `typeVal` fields are grounded in frontend consumers; the assumption that user/info includes active address is explicit and may require adjustment from sanitized user reports.

## Transport and writes

The browser proxy accepts an outer POST to `/api/fetch`, containing `method`, `url`, and `body`. This means a read-only backend GET still appears as an HTTP POST to the proxy. No such customer proxy request was made during development.

The allowlist contains only identity, registered address, monthly usage and current meter reads, plus the observed value precheck. Bearer headers never follow redirects. Response size is bounded to 4 MB and exceptions contain fixed error codes. Frontend constants explicitly distinguish `no-token`, `expired-token`, and `invalid-token`; all trigger reauthentication. Unknown envelopes never count as success.

Meter state uses `checkYn`, `meterNumber`, `prevGuideline`, and `recentGuideLine`. The previous and recent values accept bare numbers or explicit cubic-metre suffixes. A returned recent value is treated as an existing submission; the adapter does not use the site's ability to modify it.

Before submission the adapter checks fresh selected-address identity, meter identity, previous register, current permission, and absence of an existing reading. It then performs the observed precheck: backend POST `/gas/api/self-meter/check`, body `guideline`, requiring `addableYn=Y`. The actual mutation is exactly one POST to `/api/formdata` with multipart field `guideline`. Headers carry `X-Backend-Method: POST` and the URI-encoded backend path `/gas/api/self-meter`. The frontend permits an optional `meterImage`; HA currently sends a numeric reading only. A tenant demanding an image must reject the precheck or write, then the user completes it on the official site. No image or fabricated photo is submitted.

A successful envelope is only an acknowledgement. The shared submission manager reads meter state again and requires the same scoped meter/cycle and actual numeric value. Uncertain writes are not automatically repeated.

## Current permission versus schedule

The frontend meter schema exposes permission now, without observed start/end schedule fields. The adapter marks its window `dynamic_window=True` and uses the current Korea date as a permission snapshot. This is not a published utility calendar. The coordinator must hide those dates as future schedule information and skip built-in deadline/reminder automation for dynamic windows. Manual submission remains enabled with live checks. Do not infer a midnight deadline or last eligible day from this snapshot.

## Billing

Monthly usage comes from `/gas/api/pay/usage`: `list` entries contain `dateVal` in YYYYMM, `amount`, `usageVal`, and sometimes `paymentKey`. Payment transaction history is deliberately not mislabelled as monthly billing. Amount and cubic-metre usage are imported; unsupported usage units produce an explicit partial-data warning rather than corrupting the bill amount. No invented day boundaries, calorific factors or tariff segments are added. Full-period cost forecasts remain unavailable where official periods/factors are absent.

## Tenant coverage

The shared adapter supports cncity, kne, ktrm, miraense, srb, gse, cwjgas, ccbgas, cydgas, cdhgas, cscgas. Provider selection exposes all eleven tenants, including seven explicitly labelled alternate EnergyTalk connections for overlapping Gasapp suppliers. Existing Gasapp IDs, account keys and histories are unchanged.

An explicit expired-token envelope or authentication HTTP failure latches the client into reauthentication-required state. Subsequent requests stop until a new client is created after reauthentication. Ordinary network errors do not expire the session. Submission-time failures retain the existing uncertain-result handling.

Public frontend rechecked on 2026-09-09. The official multipart helper still sets `X-Backend-Method` and URI-encoded `X-Backend-Url` headers, so this transport is retained. No authenticated request or reading submission was performed.

## Sources and reproducibility

The issue corrections distinguish missing-token evidence from session-lifetime claims: [research issue #22](https://github.com/mahlernim/gas-self-meter-ai/issues/22). All implementation endpoints above were independently found in the current public frontend scripts. Public route HTML was fetched only to discover the script URLs. No login, failed OAuth exchange, protected customer call, precheck, registration or meter submission was executed.

Observed public script URLs and SHA-256 of the decoded UTF-8 text:

- [2237-68bbb5ac42e6767d.js](https://tsc-cdn.entropykorea.com/real-front/20260828-004050-f9c9ca7/_next/static/chunks/2237-68bbb5ac42e6767d.js) — `904b48c6c3b9310818ab5e7ff9872fbad47c39a13bcd5b57d57141db2f6eb22e`
- [3822-5089e9fd9982d3fa.js](https://tsc-cdn.entropykorea.com/real-front/20260828-004050-f9c9ca7/_next/static/chunks/3822-5089e9fd9982d3fa.js) — `a5db21f5e1c90c7971a546f677f536c84b72984ed80f269fc7ec1ed0c0d70388`
- [page-a51d7e5c5c61649e.js](https://tsc-cdn.entropykorea.com/real-front/20260828-004050-f9c9ca7/_next/static/chunks/app/(pages)/gas/self-meter/page-a51d7e5c5c61649e.js) — `022818de133edaa76c045e9a9d735e672c75141f28563425f30919d4ce38967d`
- [page-5416aabb47d37c5a.js](https://tsc-cdn.entropykorea.com/real-front/20260828-004050-f9c9ca7/_next/static/chunks/app/(pages)/gas/charge/recent-usage/page-5416aabb47d37c5a.js) — `bc84453b535f3de0f76e8bd6a758933abdea170f92e7d418ab34ee2ba572d92b`
- [page-28fbd28bebe47fbe.js](https://tsc-cdn.entropykorea.com/real-front/20260828-004050-f9c9ca7/_next/static/chunks/app/(pages)/gas/choice/page-28fbd28bebe47fbe.js) — `3a0390db7dc3a743f771efac5c964fe493182a2eba1463d997b834750086e107`

Offline verification: `tests/test_energytalk_adapter.py`, 42 passing tests covering all tenant scope fixtures, selected-address isolation, incomplete data, units, actual proxy/multipart shapes, precheck rejection, uncertain writes, no retries and authentication errors. Real-account compatibility remains unverified and user reports should include only redacted field names/types and fixed error codes.

Submission identity is stable across daily permission snapshots: tenant/contract, meter and canonical previous official reading form the cycle ID. Midnight, a different bill month, or a disappearing receipt never resets duplicate protection. An unchanged official baseline during a zero-usage month deliberately remains locked.
