from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class AddressAssessment:
    raw: str
    normalized: str
    country_code: str
    matched_service_area: str = ""

    @property
    def is_in_service_area(self) -> bool:
        return bool(self.matched_service_area)


def normalize_address_text(value: str) -> str:
    """Normalize comparison text without inventing or translating address parts."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("־", " ").replace("-", " ")
    value = re.sub(r"[^\w\u0590-\u05ff]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def assess_service_area(address: str, business) -> AddressAssessment:
    """Match only configured city names and aliases; unknown text remains unknown."""
    normalized = normalize_address_text(address)
    aliases = getattr(business, "service_area_aliases", {})
    for canonical in business.service_area:
        candidates = [canonical, *aliases.get(canonical, [])]
        for candidate in candidates:
            candidate_normalized = normalize_address_text(candidate)
            if candidate_normalized and re.search(
                rf"(?:^|\s){re.escape(candidate_normalized)}(?:$|\s)", normalized
            ):
                return AddressAssessment(
                    raw=address,
                    normalized=normalized,
                    country_code=business.address_default_country,
                    matched_service_area=canonical,
                )
    return AddressAssessment(
        raw=address,
        normalized=normalized,
        country_code=business.address_default_country,
    )
