"""Local metric projection for shape measurement (D3 / D16).

Longitude and latitude are never treated as planar coordinates. Every metric
quantity used by the geometry pipeline (area, perimeter, arc-length sampling)
is computed in a local azimuthal equidistant projection centred on the data,
while anything placed on a map stays in WGS84 lon/lat.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np

PROJ_TEMPLATE = "+proj=aeqd +lat_0={lat0} +lon_0={lon0} +ellps=WGS84 +units=m +no_defs"

Bounds = tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat


@dataclass(frozen=True)
class AeqdProjection:
    """Azimuthal equidistant projection centred on ``(lon0, lat0)``."""

    lon0: float
    lat0: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat0 <= 90.0:
            raise ValueError(f"lat0 must be within [-90, 90], got {self.lat0}")
        if not -180.0 <= self.lon0 <= 180.0:
            raise ValueError(f"lon0 must be within [-180, 180], got {self.lon0}")

    @classmethod
    def from_bounds(cls, bounds: Bounds) -> AeqdProjection:
        """Centre the projection on a ``(min_lon, min_lat, max_lon, max_lat)`` box."""
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bounds)
        return cls(lon0=(min_lon + max_lon) / 2.0, lat0=(min_lat + max_lat) / 2.0)

    @property
    def proj_string(self) -> str:
        return PROJ_TEMPLATE.format(lat0=self.lat0, lon0=self.lon0)

    @cached_property
    def _forward(self) -> Any:
        from pyproj import Transformer

        return Transformer.from_proj("EPSG:4326", self.proj_string, always_xy=True)

    @cached_property
    def _backward(self) -> Any:
        from pyproj import Transformer

        return Transformer.from_proj(self.proj_string, "EPSG:4326", always_xy=True)

    def to_meters(self, lonlat: np.ndarray) -> np.ndarray:
        """Project an (N, 2) array of lon/lat degrees to metres."""
        arr = np.asarray(lonlat, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("lonlat must have shape (N, 2)")
        x, y = self._forward.transform(arr[:, 0], arr[:, 1])
        return np.column_stack((x, y))

    def to_lonlat(self, meters: np.ndarray) -> np.ndarray:
        """Inverse of :meth:`to_meters`."""
        arr = np.asarray(meters, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("meters must have shape (N, 2)")
        lon, lat = self._backward.transform(arr[:, 0], arr[:, 1])
        return np.column_stack((lon, lat))

    def project_geometry(self, geometry: Any) -> Any:
        """Reproject a shapely geometry from lon/lat to metres."""
        import shapely

        return shapely.transform(geometry, self.to_meters)

    def unproject_geometry(self, geometry: Any) -> Any:
        """Reproject a shapely geometry from metres back to lon/lat."""
        import shapely

        return shapely.transform(geometry, self.to_lonlat)

    def describe(self) -> dict[str, Any]:
        return {
            "name": "aeqd",
            "lon0": self.lon0,
            "lat0": self.lat0,
            "proj_string": self.proj_string,
            "units": "m",
            "input_crs": "EPSG:4326",
        }
