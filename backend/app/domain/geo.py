from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Optional, Tuple

# APPROXIMATE ZIP centroids for Buffalo / WNY (typed by hand - verify before relying on them).
ZIP_CENTROIDS = {
    "14201": (42.902, -78.889), "14202": (42.890, -78.870), "14203": (42.880, -78.868),
    "14204": (42.883, -78.857), "14206": (42.883, -78.823), "14207": (42.956, -78.904),
    "14208": (42.918, -78.850), "14209": (42.915, -78.870), "14210": (42.853, -78.814),
    "14211": (42.917, -78.824), "14212": (42.897, -78.820), "14213": (42.920, -78.889),
    "14214": (42.950, -78.835), "14215": (42.925, -78.800), "14216": (42.954, -78.865),
    "14217": (42.968, -78.880), "14218": (42.833, -78.825), "14219": (42.790, -78.830),
    "14220": (42.845, -78.800), "14221": (42.970, -78.745), "14222": (42.930, -78.879),
    "14223": (42.975, -78.870), "14224": (42.830, -78.750), "14225": (42.930, -78.750),
    "14226": (42.965, -78.800), "14227": (42.900, -78.740), "14228": (42.980, -78.760),
    "14260": (42.955, -78.820), "14043": (42.900, -78.700), "14086": (42.900, -78.620),
    "14120": (43.040, -78.860), "14150": (42.995, -78.880), "14127": (42.770, -78.750),
    "14072": (43.000, -78.960), "14052": (42.770, -78.620), "14059": (42.830, -78.630),
}

ROAD_FACTOR = 1.3      # straight line -> road distance
AVG_MPH = 30.0         # city/suburban driving


class GeoService:
    EARTH_RADIUS_MILES = 3958.8

    def distance_miles(self, lat1: Optional[float], lng1: Optional[float],
                       lat2: Optional[float], lng2: Optional[float]) -> Optional[float]:
        if None in (lat1, lng1, lat2, lng2):
            return None
        d_lat, d_lng = radians(lat2 - lat1), radians(lng2 - lng1)
        a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
        return round(2 * self.EARTH_RADIUS_MILES * asin(sqrt(a)), 1)

    @staticmethod
    def zip_to_latlng(zip_code: Optional[str]) -> Optional[Tuple[float, float]]:
        return ZIP_CENTROIDS.get((zip_code or "").strip()[:5])

    def drive_minutes(self, origin: Optional[Tuple[float, float]], lat: Optional[float],
                      lng: Optional[float]) -> Optional[int]:
        """Rough estimate (straight-line x 1.3 at 30 mph), not routed."""
        if origin is None:
            return None
        miles = self.distance_miles(origin[0], origin[1], lat, lng)
        return None if miles is None else round(miles * ROAD_FACTOR / AVG_MPH * 60)
