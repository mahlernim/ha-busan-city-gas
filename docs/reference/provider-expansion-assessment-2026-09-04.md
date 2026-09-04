# Provider expansion architecture and evidence

This reference describes the provider expansion in v0.6.0. For installation and current capabilities, see [supported providers](../providers.md).

## Coverage and compatibility

**똑똑 자가검침 AI** offers 30 provider choices: eight SK E&S regions, fourteen Gasapp brands, Samchully, four EnergyTalk suppliers, two Daesung companies and Haeyang. Each has a read/write adapter; new connection families are experimental and may require adjustments for individual contracts. Implementation and synthetic tests do not establish successful operation with every provider.

Supplier identity, connection channel and platform tenant code are separate. Gasapp's fourteen brands map to eighteen company codes, including five regional Chambit codes. EnergyTalk overlaps Gasapp, so platform tenant counts are not added to the provider total. Jeonbuk Energy Service and Jeonbuk City Gas remain separate suppliers. MC Energy includes its Mokpo City Gas alias. Myungsung Power Green is excluded because no usable customer protocol has been identified.

The visible name is shared with the Android app; use “Home Assistant integration” when distinguishing the products. The existing `busan_city_gas` domain, component directory, unique IDs, storage keys, service/WebSocket names, panel path and ZIP name remain unchanged. The repository URL is unchanged. Existing installations do not require deletion or re-registration.

## Authentication and write support

| Connection family | Authentication | Write behavior and limitations |
| --- | --- | --- |
| SK E&S | Website username/password | Existing submission and residential tariff support |
| Gasapp | Identity information, required terms and SMS | Explicit service registration/channel consent when required; meter preflight and receipt reconciliation |
| Samchully | Website username/password | Numeric submission using the observed manual-entry DTO; no fabricated image |
| EnergyTalk | Existing selected-address session token | Current permission and selected address are checked; no inferred deadline or built-in deadline schedule |
| Daesung Energy / Clean Energy | Website username/password | Company-specific paths and authenticated labelled-form discovery; ambiguous or JavaScript-only forms are unsupported |
| Haeyang | Website username/password | BizMOB WEB login and SELF101 write; no native AES key is required by the public web branch |

Experimental support is available for users to test with their own accounts. Automatic submission remains opt-in. Authentication, service membership changes and meter submissions are distinct operations. Reauthentication must preserve prior uncertain-attempt records. A response acknowledgement is not a receipt: the integration re-reads the contract's meter state and verifies the accepted value.

## Shared architecture

- **Provider-aware clients:** each family owns its authentication, contract mapping, billing parser and submission transport. SK identifiers are preserved; new contract and meter keys are namespaced and opaque.
- **Partial billing:** monthly amounts and usage remain visible when detailed periods, cumulative readings, heat factors or tariff segments are absent. Missing values are not replaced with invented zero amounts or dates.
- **Tariff capabilities:** account and submission support do not imply tariff support. SK residential estimates remain available; other families display the official billing information they supply.
- **Submission state:** fresh identity, eligibility, prior reading and existing-receipt checks precede a single write. Uncertain writes are not automatically repeated. Provider order/baseline cycle identities preserve duplicate protection when display dates change.
- **Consent:** Gasapp service registration and channel changes require explicit confirmation. SMS identity input is retained only for authentication. EnergyTalk login and address selection remain on the official site.

## Reusable evidence and limits

The MIT-licensed [Android reference](https://github.com/mahlernim/gas-self-meter-ai/tree/cb615cb8f8c9f398a3a89b871379e22127241996) supplied protocol logic and synthetic cases for SMS authentication, partial bills, meter changes, consent, session expiry and uncertain submissions. Applicable attribution is preserved in [NOTICE](../../NOTICE). Android UI, storage and background scheduling are replaced with Home Assistant flows, coordinators and persistent per-contract state.

The issue discussions contain corrections that matter when extending an adapter:

- Similar Daesung login forms do not prove interchangeable authenticated schemas.
- Haeyang's older native-AES uncertainty does not block its separately observed WEB branch. SELF300 is a cost preview; SELF101 submits a reading.
- Samchully cancellation is not implemented based on unrelated order flows or failed endpoint probes.
- Provider menus and failed authentication responses establish neither successful account access nor submission compatibility.
- Public-source analysis and synthetic fixtures are distinguished from live customer evidence. Tests do not claim real-account verification for the new adapters.

Detailed current protocol evidence and assumptions: [EnergyTalk](energytalk-protocol.md), [Daesung](daesung-protocol.md), [Haeyang](haeyang-protocol.md).

## Sources

- [Expansion overview and corrections, #16](https://github.com/mahlernim/gas-self-meter-ai/issues/16)
- [Gasapp, #15](https://github.com/mahlernim/gas-self-meter-ai/issues/15)
- [Samchully, #19](https://github.com/mahlernim/gas-self-meter-ai/issues/19)
- [EnergyTalk, #22](https://github.com/mahlernim/gas-self-meter-ai/issues/22)
- [Daesung family, #21](https://github.com/mahlernim/gas-self-meter-ai/issues/21)
- [Haeyang and corrections, #45](https://github.com/mahlernim/gas-self-meter-ai/issues/45)
- [Tariffs, #46](https://github.com/mahlernim/gas-self-meter-ai/issues/46)
- [Official Gasapp service areas](https://www.gasapp.co.kr/)
- [Home Assistant manifest name and domain](https://developers.home-assistant.io/docs/creating_integration_manifest/)
