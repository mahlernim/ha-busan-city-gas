"""Supported SK E&S regional gas providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    code: str
    path: str
    regions: tuple[tuple[str, str], ...] = (("default", "기본 지역"),)
    profiles: tuple[tuple[str, str, str], ...] = (("residential", "취사전용", "취사"),)
    threshold_mj: str | None = None

    @property
    def website(self) -> str:
        return f"https://www.skens.com/{self.path}/login/login.do"

    @property
    def billing_url(self) -> str:
        return f"https://ebpp.skens.com/{self.path}/charge/ask.do"


PROVIDERS = {
    item.id: item
    for item in (
        Provider(
            "busan",
            "부산도시가스",
            "C000",
            "busan",
            profiles=(
                ("residential", "취사전용", "취사전용"),
                ("individual", "개별난방", "개별난방"),
                ("central", "중앙난방", "중앙난방"),
            ),
            threshold_mj="516",
        ),
        Provider(
            "koone",
            "코원에너지서비스",
            "B000",
            "koone",
            (("seoul", "서울"), ("gyeonggi", "경기")),
            (("residential", "취사용", "취사"), ("heating", "난방용", "주택용 난방")),
        ),
        Provider(
            "cheongju",
            "충청에너지서비스",
            "D000",
            "cheongju",
            profiles=(
                ("residential", "취사전용", "취사전용"),
                ("individual", "개별난방", "개별난방"),
                ("central", "중앙난방", "중앙난방"),
            ),
        ),
        Provider(
            "gumi",
            "영남에너지서비스 구미",
            "E000",
            "gumi",
            profiles=(
                ("residential", "취사용", "취사용"),
                ("individual", "개별난방", "개별난방"),
                ("central", "중앙난방", "중앙난방"),
            ),
            threshold_mj="522",
        ),
        Provider(
            "pohang",
            "영남에너지서비스 포항",
            "F000",
            "pohang",
            profiles=(
                ("residential", "취사용", "취사"),
                ("individual", "개별난방", "개별난방"),
                ("central", "중앙난방", "중앙난방"),
            ),
        ),
        Provider(
            "jeonnam",
            "전남도시가스",
            "G000",
            "jeonnam",
            profiles=(("residential", "취사전용", "취사"), ("individual", "개별난방", "개별난방")),
        ),
        Provider(
            "gangwon",
            "강원도시가스",
            "J000",
            "gangwon",
            profiles=(
                ("residential", "취사전용", "주택및난방용"),
                ("individual", "개별난방", "개별난방"),
                ("central", "중앙난방", "중앙난방"),
            ),
        ),
        Provider(
            "jeonbuk",
            "전북에너지서비스",
            "K000",
            "jeonbuk",
            profiles=(
                ("residential", "취사난방", "취사난방"),
                ("district", "지역난방", "지역난방"),
                ("central", "중앙난방", "중앙난방"),
            ),
        ),
    )
}


def get_provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise ValueError("unsupported_provider") from None


def default_region(provider: Provider) -> str:
    return provider.regions[0][0]


def default_profile(provider: Provider) -> str:
    return provider.profiles[0][0]


def profile_match(provider: Provider, profile: str) -> str:
    for value, _label, match in provider.profiles:
        if value == profile:
            return match
    raise ValueError("unsupported_tariff_profile")


def region_label(provider: Provider, region: str) -> str:
    for value, label in provider.regions:
        if value == region:
            return label
    raise ValueError("unsupported_tariff_region")
