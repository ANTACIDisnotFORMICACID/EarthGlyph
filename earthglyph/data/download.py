"""Reproducible download of Overture Maps GeoParquet extracts (P0-1).

The public entry point is :func:`download_theme`. It resolves the Overture
release, fetches the bbox extract (``fetch_method="duckdb"``: official STAC
index + DuckDB httpfs range reads; ``"cli"``: the official ``overturemaps``
download command), verifies the output file, and writes a JSON sidecar
recording source, version, bbox, checksum and download time so any run can be
reproduced and audited.

Existing valid data is never overwritten unless ``force=True``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

SOURCE_NAME = "Overture Maps (Base theme), https://docs.overturemaps.org/"

BBox = tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat

Fetch = Callable[[BBox, str, Path, int], None]


class DownloadError(RuntimeError):
    """Raised when a download fails or the produced file is not valid."""


def parse_bbox(text: str) -> BBox:
    """Parse ``min_lon,min_lat,max_lon,max_lat`` into a validated BBox."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError(
            "bbox must be 4 comma-separated values: min_lon,min_lat,max_lon,max_lat"
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"bbox values must be numbers: {text!r}") from exc
    if not -180 <= min_lon <= 180 or not -180 <= max_lon <= 180:
        raise ValueError("longitude must be within [-180, 180]")
    if not -90 <= min_lat <= 90 or not -90 <= max_lat <= 90:
        raise ValueError("latitude must be within [-90, 90]")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("bbox must have min < max (west<east, south<north)")
    return (min_lon, min_lat, max_lon, max_lat)


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_parquet(path: Path) -> int:
    """Return the row count of a readable Parquet file, or raise DownloadError."""
    if not path.is_file():
        raise DownloadError(f"output file missing: {path}")
    if path.stat().st_size == 0:
        raise DownloadError(f"output file is empty: {path}")
    try:
        import pyarrow.parquet as pq

        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception as exc:  # parquet parsing errors vary widely
        raise DownloadError(f"output file is not a readable Parquet: {path}: {exc}") from exc


def resolve_latest_release() -> str:
    """Ask the installed overturemaps client for the current latest release.

    Retries with backoff: this environment intermittently hits Windows
    ephemeral-port conflicts (WinError 10048) on the STAC fetch.
    """
    import time

    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            from overturemaps.core import get_latest_release

            return str(get_latest_release())
        except Exception as exc:
            last_exc = exc
            time.sleep(2.0 * (attempt + 1))
    raise DownloadError(f"cannot resolve latest Overture release: {last_exc}")


def _cli_fetch(bbox: BBox, release: str, out_path: Path, timeout_s: int) -> None:
    """Fetch via the official overturemaps CLI in this project's environment."""
    if shutil.which("overturemaps") is None and not _cli_module_available():
        raise DownloadError("overturemaps CLI not available in this environment")
    cmd = [
        sys.executable,
        "-m",
        "overturemaps",
        "download",
        "--bbox",
        ",".join(str(v) for v in bbox),
        "--type=water",
        "-f",
        "geoparquet",
        "-r",
        release,
        "-o",
        str(out_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise DownloadError(
            f"overturemaps CLI failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )


def _cli_module_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("overturemaps") is not None


STAC_COLLECTIONS_URL = "https://stac.overturemaps.org/{release}/collections.parquet"


def _fetch_via_duckdb(bbox: BBox, release: str, out_path: Path, timeout_s: int) -> None:
    """Extract bbox-intersecting water rows over plain HTTPS with DuckDB.

    Why not the official CLI's S3 path here: this machine's AWS SDK (pyarrow
    S3) direct connections get truncated responses and the system proxy is
    flaky, while DuckDB httpfs range-reads of the same S3 objects work
    reliably and read only the parquet row groups matching the bbox — a tiny
    fraction of the ~1.2 GB source files. File URLs and bbox filtering follow
    the official STAC-geoparquet index, the same source the CLI uses.
    """
    import json as _json

    import duckdb
    import pyarrow.parquet as pq

    xmin, ymin, xmax, ymax = bbox
    stac_url = STAC_COLLECTIONS_URL.format(release=release)
    con = duckdb.connect()
    con.execute("SET http_timeout=120000")
    try:
        con.execute("LOAD httpfs")
    except Exception:
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")

    files = con.execute(
        f"""
        SELECT assets.aws.href href
        FROM read_parquet('{stac_url}')
        WHERE collection = 'water' AND type = 'Feature'
          AND bbox.xmin < {xmax} AND bbox.xmax > {xmin}
          AND bbox.ymin < {ymax} AND bbox.ymax > {ymin}
        """
    ).fetchall()
    urls = [r[0] for r in files]
    if not urls:
        raise DownloadError(f"STAC lists no water files for release {release} in bbox {bbox}")

    url_list = ", ".join(f"'{u}'" for u in urls)
    query = f"""
        SELECT * FROM read_parquet([{url_list}])
        WHERE bbox.xmin < {xmax} AND bbox.xmax > {xmin}
          AND bbox.ymin < {ymax} AND bbox.ymax > {ymin}
    """
    arrow = con.sql(query).to_arrow_table()
    if arrow.num_rows == 0:
        raise DownloadError(
            f"no water features intersect bbox {bbox} in release {release}; "
            "check the bbox coordinates or pick another region"
        )

    # Emit valid GeoParquet: WKB geometry + GeoParquet 1.0 "geo" key-value
    # metadata (WGS84), matching Overture's own conventions.
    geo_meta = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": [],
                "crs": {
                    "$schema": "https://proj.org/schemas/v0.5/projjson.schema.json",
                    "type": "GeographicCRS",
                    "name": "WGS 84 (G730)",
                    "datum": {
                        "type": "GeodeticReferenceFrame",
                        "name": "World Geodetic System 1984",
                        "ellipsoid": {
                            "name": "WGS 84",
                            "semi_major_axis": 6378137,
                            "inverse_flattening": 298.257223563,
                        },
                    },
                    "coordinate_system": {
                        "subtype": "ellipsoidal",
                        "axis": [
                            {"name": "Geodetic longitude", "abbreviation": "Lon",
                             "direction": "east", "unit": "degree"},
                            {"name": "Geodetic latitude", "abbreviation": "Lat",
                             "direction": "north", "unit": "degree"},
                        ],
                    },
                    "id": {"authority": "EPSG", "code": 4326},
                },
            }
        },
    }
    existing = arrow.schema.metadata or {}
    existing[b"geo".decode()] = _json.dumps(geo_meta)
    table = arrow.replace_schema_metadata(existing)
    pq.write_table(table, out_path, compression="zstd")


FETCHERS: dict[str, Fetch] = {"cli": _cli_fetch, "duckdb": _fetch_via_duckdb}
FETCH_LABELS = {"cli": "overturemaps-cli", "duckdb": "duckdb-httpfs"}


def download_theme(
    bbox: BBox,
    out_path: Path,
    *,
    theme: str = "water",
    release: str | None = None,
    force: bool = False,
    timeout_s: int = 900,
    fetch: Fetch | None = None,
    fetch_method: str = "duckdb",
) -> dict[str, Any]:
    """Download one theme for ``bbox`` and record provenance in a sidecar.

    ``fetch_method`` selects a built-in fetcher (``"duckdb"`` or ``"cli"``);
    ``fetch`` overrides it with a callable, which tests use to stay offline.
    Returns the sidecar metadata dict. Re-running with the same bbox and
    release reuses a valid existing file (idempotent) unless ``force``.
    """
    if fetch_method not in FETCHERS:
        raise ValueError(
            f"unknown fetch_method {fetch_method!r}: expected {sorted(FETCHERS)}"
        )
    if theme != "water":
        # Both fetchers read the water collection; mislabelling provenance
        # would break reproducibility, so refuse instead of guessing.
        raise NotImplementedError(f"only the water theme is implemented (got {theme!r})")
    fetcher = fetch or FETCHERS[fetch_method]
    out_path = Path(out_path)
    sidecar = out_path.with_suffix(out_path.suffix + ".meta.json")
    release = release or resolve_latest_release()

    if out_path.exists() and not force:
        try:
            rows = verify_parquet(out_path)
            if rows == 0:
                raise DownloadError("existing file has 0 rows")
        except DownloadError as exc:
            print(f"existing file invalid ({exc}), re-downloading: {out_path}", file=sys.stderr)
        else:
            print(f"reusing existing valid file ({rows} rows): {out_path}", file=sys.stderr)
            meta: dict[str, Any] = {}
            if sidecar.is_file():
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            meta["source"] = meta.get("source", SOURCE_NAME)
            meta.setdefault("release", release)
            meta["note"] = "reused existing file"
            meta["file"] = {
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size,
                "sha256": sha256_of(out_path),
                "rows": rows,
            }
            sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            return meta

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fetcher(bbox, release, out_path, timeout_s)

    rows = verify_parquet(out_path)

    meta = {
        "source": SOURCE_NAME,
        "dataset": f"Overture Maps base theme, type={theme}",
        "release": release,
        "download_time_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "bbox": {
            "min_lon": bbox[0],
            "min_lat": bbox[1],
            "max_lon": bbox[2],
            "max_lat": bbox[3],
            "order": "min_lon,min_lat,max_lon,max_lat",
        },
        "format": "geoparquet",
        "fetch_method": "custom" if fetch is not None else FETCH_LABELS[fetch_method],
        "reproduce": (
            f"python scripts/download_data.py --bbox={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
            f" --output <path> --release {release} --force"
            f" --fetch-method {fetch_method}"
        ),
        "cli_equivalent": (
            f"python -m overturemaps download --bbox={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
            f" --type={theme} -f geoparquet -r {release} -o {out_path}"
        ),
        "file": {
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "sha256": sha256_of(out_path),
            "rows": rows,
        },
    }
    sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta
