"""Supported Korean gas providers and their connection capabilities."""

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
    family: str = "skens"
    company_codes: tuple[str, ...] = ()
    homepage: str = ""

    @property
    def supports_submission(self) -> bool:
        return self.family in ("skens", "gasapp", "samchully", "energytalk", "daesung", "haeyang")

    @property
    def supports_deadline(self) -> bool:
        return self.supports_submission and self.family != "energytalk"

    @property
    def supports_tariff(self) -> bool:
        return self.family == "skens"

    @property
    def website(self) -> str:
        if self.homepage:
            return self.homepage
        return f"https://www.skens.com/{self.path}/login/login.do"

    @property
    def billing_url(self) -> str:
        if self.family != "skens":
            return "https://app.gasapp.co.kr/" if self.family == "gasapp" else self.website
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

# Brand and platform company identity are separate (Chambit has five codes).
for _id, _name, _codes in (
    ("seoul", "서울도시가스", ("1",)),
    ("incheon", "인천도시가스", ("2",)),
    ("jeju", "제주도시가스", ("3",)),
    ("jb", "JB", ("4",)),
    ("daeryun", "대륜E&S", ("5",)),
    ("yesco", "예스코", ("6",)),
    ("gunsan", "군산도시가스", ("7",)),
    ("kiturami", "귀뚜라미에너지", ("8",)),
    ("chambit", "참빛도시가스 계열", ("9", "10", "11", "12", "13")),
    ("kyungdong", "경동도시가스", ("14",)),
    ("mcenergy", "MC에너지 (목포도시가스)", ("15",)),
    ("seohae", "미래엔서해에너지", ("16",)),
    ("daehwa", "대화도시가스", ("17",)),
    ("jeonbukgas", "전북도시가스", ("18",)),
):
    PROVIDERS[_id] = Provider(
        _id,
        _name,
        f"gasapp:{_id}",
        "",
        family="gasapp",
        company_codes=_codes,
        homepage="https://www.gasapp.co.kr/",
    )
PROVIDERS["samchully"] = Provider(
    "samchully",
    "삼천리",
    "samchully",
    "",
    family="samchully",
    homepage="https://cs.samchully.co.kr/",
)

for _id, _name, _tenant in (
    ("cncity", "CNCITY에너지", "cncity"),
    ("gyeongnam", "경남에너지", "kne"),
    ("seorabeol", "서라벌도시가스", "srb"),
    ("gse", "지에스이", "gse"),
):
    PROVIDERS[_id] = Provider(
        _id,
        _name,
        f"energytalk:{_tenant}",
        _tenant,
        family="energytalk",
        homepage="https://energytalk.ai/",
    )

for _id, _name, _host in (
    ("daesung", "대성에너지", "https://cyber.daesungenergy.com"),
    ("daesungclean", "대성청정에너지", "https://www.daesungcleanenergy.co.kr"),
):
    PROVIDERS[_id] = Provider(_id, _name, _id, "", family="daesung", homepage=_host)

PROVIDERS["haeyang"] = Provider(
    "haeyang",
    "해양에너지",
    "haeyang",
    "",
    family="haeyang",
    homepage="https://m.hyenergy.co.kr/",
)


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
