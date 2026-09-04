# Haeyang mobile-web protocol

Reviewed 2026-09-04 using public JavaScript GET requests only. No authentication probes, account queries or meter submissions were sent. Account behavior remains experimental; successful customer fixtures are not a prerequisite to testing the implementation.

## Findings that supersede issue #45

[Issue #45](https://github.com/mahlernim/gas-self-meter-ai/issues/45) correctly retracts unsupported claims about native AES, but the current public web code provides a usable route:

- [hyBizMOB.js](https://m.hyenergy.co.kr/bizmob/contents/common/js/hyBizMOB.js): `Device.getDecAES` explicitly branches on `hyUtil.isWeb()` and returns the supplied value through JSON serialization. No AES key or IV is needed on the WEB route. Encrypted/non-object bodies are unsupported; the adapter does not guess native encryption.
- The same file defines form encoding `message=<URL-encoded JSON envelope>`, the WEB header and the `LOGIN.json` bootstrap. It wraps `LOGIN01` in `legacy_message`, with `os_type=mobileweb`, empty outer user/password, and `legacy_trcode=LOGIN01`. Responses contain `body.legacy_message`. Browser session cookies are retained by the per-account HTTP session.
- [LOG0100.js](https://m.hyenergy.co.kr/bizmob/contents/LOG/js/LOG0100.js) provides the normal username plus MD5/SHA-256 password fields. It uses the bootstrap after a timeout and the direct transaction once initialized. The adapter starts with the bootstrap, avoiding a guessed initial native session.
- [hyConstants.js](https://m.hyenergy.co.kr/bizmob/contents/common/js/hyConstants.js) supplies public application identifier `HYCGANP0` and `IFFLAG.WEB=W`. These are public client metadata, not customer credentials.

## Implemented queries and writes

| Transaction | Observed public source | Adapter behavior |
| --- | --- | --- |
| LOGIN / LOGIN01 | LOG0100 and hyBizMOB | Username/password authentication; no saved hashes outside HA credential storage |
| MYPAGE3 | [MAI0100.js](https://m.hyenergy.co.kr/bizmob/contents/MAI/js/MAI0100.js) | payerList with both I_PAYERNO/I_PAYERNM and PAYERNO/PAYERNM; contract identity and optional installation identity |
| BILL001 | [FEE_COM0100.js](https://m.hyenergy.co.kr/bizmob/contents/FEE/js/FEE_COM0100.js) | Actual monthly billing query; YEARMONTH, NOTICE_AMT, CONSUME_QTY |
| BILL002 | FEE_COM0100 | Optional detailed billing by NOTICENO/PAYERNO/YYYYMM; usage dates, installation ID and cumulative reading; preserve summary on detail failure |
| SELF100 | [NEW_SEF0101.js](https://m.hyenergy.co.kr/bizmob/contents/NEW/js/NEW_SEF0101.js) | INSTALLNO/PAYERNO; RTNCD, IT_TAB MTORDERNO, PAYMENTDATE, PREV_INDCT, NREVB_INDCT, METERDATE_CM |
| SELF101 | [MYP_SEP0101.js](https://m.hyenergy.co.kr/bizmob/contents/MYP/js/MYP_SEP0101.js) | Actual write: CURR_INDCT, MTORDERNO, IFFLAG=W |

`SELF300` is a cost preview, **not** the meter submission endpoint. It is deliberately absent from the submission adapter. `SELF201` changes service membership; this adapter does not silently enroll or cancel membership.

Money conversion follows the public FEE_COM0100 `toCommaNumber` implementation: multiply NOTICE_AMT by 100 and round to integer won. Missing details do not prevent displaying a monthly amount and usage. No guessed tariff is applied.

## Assumptions and reconciliation

The display window uses the month after SELF100 METERDATE (the documented previous-month reading date), falling back to the current Korean month if absent, with the public PAYMENTDATE groups A=1–5, B=6–10, C=11–15, S=last two days. Only SELF100 RTNCD `00` makes it eligible; `01` closes submission. The implementation does not reproduce the public script's apparent getDay/getDate rollover bug. An accepted reading needs METERDATE_CM inside that period; an unexplained nonzero reading blocks overwrite. A stale order therefore retains the same cycle across month changes and restarts, preserving the coordinator’s uncertain-write lock. Cycle identity is an opaque account/installation/MTORDERNO key independent of display dates, including when METERDATE is absent. Future reports may refine irregular billing periods.

Submission refreshes the current order, account identity, previous reading and period before sending once. It accepts only integer cumulative readings at or above the previous reading. Changed orders, existing receipts or ambiguous observations stop the write. Timeout after dispatch becomes uncertain and is never automatically retried. The coordinator must re-read SELF100 to confirm acceptance; successful response headers alone are not receipts.

The real-account schema is not confirmed. In particular, installment/contract associations, response error codes and SAP amount scaling are derived from the current web application. Errors contain stable codes and never response bodies. Synthetic tests cover the observed form semantics, authentication envelopes, currency scaling, partial bills, order checks, uncertain writes and leap-year periods.

## Public source fingerprint

The following fingerprints describe the downloaded UTF-8 text after newline normalization where applied, not server byte-for-byte content. Full third-party scripts are not redistributed.

- `bizMOB/bizMOB3.js` SHA-256 `31c235506b85bf8cf93c9a423f8a555b8b9fb06c476d7d5060456550320e4f1e`
- `common/js/hyBizMOB.js` SHA-256 `b845f7339b788d6cc124a433befbd065df67f3a394b010e0a1e0e0b9833c6bfd`
- `common/js/hyConstants.js` SHA-256 `877d4cf4ca42d4db75e36cccf41ec0c5e69eb5e98b500c039576ad9ec5462c8c`
- `common/js/hyUtil.js` SHA-256 `1aa5dc4d72ef11db9eb3e158811de79645b0278f75b4c4367457a049c0c4808d`
- `FEE/js/FEE_COM0100.js` SHA-256 `f35c18d5694c52db045c4f535436fe3e6abf29fb0520c34b61e57ca317c9cbbf`
- `LOG/js/LOG0100.js` SHA-256 `73536bf152bf6268973a6f8122fe5f1688e5343dd1fe5fef7fe3587c705c9ecb`
- `MAI/js/MAI0100.js` SHA-256 `9f46bd6bc8af54ca6a9d11f84261017b54dcbad86a157e052a2f4c41854d6c79`
- `MYP/js/MYP_SEP0101.js` SHA-256 `abdb12580f765aa8f53ef42051f726973fd842640d724a0472afb75aded14e51`
- `NEW/js/NEW_SEF0101.js` SHA-256 `495060a90fa6c2d1c89947c01b30be7f9afa01b3f81ca7e1300fccfc0a56aa8a`
