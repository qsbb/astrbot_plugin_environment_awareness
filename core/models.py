from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Location:
    query: str
    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str = ""
    country_code: str = ""
    admin1: str = ""
    admin2: str = ""
    admin3: str = ""
    admin4: str = ""

    @property
    def display_name(self) -> str:
        parts = [self.name, self.admin1, self.country]
        return " · ".join(dict.fromkeys(part for part in parts if part))

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.display_name or self.name,
            "timezone": self.timezone,
        }

    def profile_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_profile(cls, value: Any) -> Location | None:
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                query=str(value.get("query") or ""),
                name=str(value.get("name") or ""),
                latitude=float(value["latitude"]),
                longitude=float(value["longitude"]),
                timezone=str(value.get("timezone") or "UTC"),
                country=str(value.get("country") or ""),
                country_code=str(value.get("country_code") or "").upper(),
                admin1=str(value.get("admin1") or ""),
                admin2=str(value.get("admin2") or ""),
                admin3=str(value.get("admin3") or ""),
                admin4=str(value.get("admin4") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class EarthquakeEvent:
    event_id: str
    title: str
    magnitude: float
    place: str
    occurred_at: str
    updated_at: str
    latitude: float
    longitude: float
    depth_km: float
    distance_km: float
    effective_distance_km: float
    relevance_radius_km: float
    relevance: str
    alert_level: str
    tsunami: bool
    source_url: str

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("latitude", None)
        data.pop("longitude", None)
        data["distance_km"] = round(self.distance_km, 1)
        data["effective_distance_km"] = round(self.effective_distance_km, 1)
        data["relevance_radius_km"] = round(self.relevance_radius_km, 1)
        data["impact_assessment"] = "距离与震级启发式筛选，不等同于官方烈度评估"
        return data


@dataclass(frozen=True, slots=True)
class CacheResult:
    value: Any
    stale: bool
    expires_at: float
