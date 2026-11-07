"""Random access to a remote ZIP archive over HTTP Range requests.

The ShapeComp Zenodo deposits are 308 MB and 2.23 GB single ZIPs. Enumerating their
central directory and pulling only the handful of members we need costs a few MB
instead of the whole archive.
"""

from __future__ import annotations

import re
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

DEFAULT_CHUNK = 1 << 20
# Zenodo's bot protection rejects browser-like User-Agents (see shapecomp_research.md §G).
USER_AGENT = "earthglyph-shape-benchmark/0.1 (python-urllib)"
_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)", re.IGNORECASE)


class HttpRangeFile:
    """A seekable, read-only file object backed by cached HTTP Range fetches."""

    def __init__(
        self,
        url: str,
        *,
        size: int | None = None,
        chunk: int = DEFAULT_CHUNK,
        timeout: float = 120.0,
    ) -> None:
        self.url = url
        self.chunk = chunk
        self.timeout = timeout
        self._pos = 0
        self._blocks: dict[int, bytes] = {}
        self._lock = threading.Lock()
        self.bytes_fetched = 0
        self.request_count = 0
        self._size = size if size is not None else self._probe_size()

    def _fetch(self, start: int, end: int) -> bytes:
        """Fetch [start, end) — tolerating servers that ignore the Range header."""
        headers = {"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end - 1}"}
        request = urllib.request.Request(self.url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload: bytes = response.read()
            status = response.status
            content_range = response.headers.get("Content-Range", "")
        self.request_count += 1
        if status == 206:
            match = _CONTENT_RANGE.search(content_range)
            if match is not None:
                self._size = int(match.group(3))
            self.bytes_fetched += len(payload)
            return payload
        # 200: the server returned the whole object; slice out what we asked for.
        self.bytes_fetched += len(payload)
        self._size = len(payload)
        return payload[start:end]

    def _probe_size(self) -> int:
        self._fetch(0, 1)
        return self._size

    def _block(self, index: int) -> bytes:
        with self._lock:
            cached = self._blocks.get(index)
            if cached is None:
                start = index * self.chunk
                cached = self._fetch(start, min(start + self.chunk, self._size))
                self._blocks[index] = cached
            return cached

    @property
    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            target = offset
        elif whence == 1:
            target = self._pos + offset
        elif whence == 2:
            target = self._size + offset
        else:
            raise ValueError(f"invalid whence: {whence}")
        if target < 0:
            raise ValueError("negative seek position")
        self._pos = target
        return self._pos

    def read(self, n: int = -1) -> bytes:
        remaining = self._size - self._pos
        if remaining <= 0:
            return b""
        count = remaining if n is None or n < 0 else min(n, remaining)
        out = bytearray()
        position = self._pos
        while len(out) < count:
            block = self._block(position // self.chunk)
            offset = position % self.chunk
            take = min(count - len(out), len(block) - offset)
            if take <= 0:
                break
            out += block[offset : offset + take]
            position += take
        self._pos = position
        return bytes(out)

    def close(self) -> None:
        self._blocks.clear()

    def __enter__(self) -> HttpRangeFile:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


@dataclass
class RemoteZip:
    """A remote ZIP plus the transfer statistics needed for a reproducibility record."""

    url: str
    chunk: int = DEFAULT_CHUNK
    _file: HttpRangeFile = field(init=False, repr=False)
    _zip: zipfile.ZipFile = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._file = HttpRangeFile(self.url, chunk=self.chunk)
        self._zip = zipfile.ZipFile(self._file)

    @property
    def archive_size(self) -> int:
        return self._file.size

    @property
    def bytes_fetched(self) -> int:
        return self._file.bytes_fetched

    @property
    def request_count(self) -> int:
        return self._file.request_count

    def infos(self) -> list[zipfile.ZipInfo]:
        return self._zip.infolist()

    def names(self) -> list[str]:
        return self._zip.namelist()

    def read(self, name: str) -> bytes:
        return self._zip.read(name)

    def extract_to(self, name: str, destination: Path) -> Path:
        """Write one member to ``destination / name``, preserving relative layout."""
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._zip.read(name))
        return target

    def stats(self) -> dict[str, int | str]:
        return {
            "url": self.url,
            "archive_size": self.archive_size,
            "bytes_fetched": self.bytes_fetched,
            "request_count": self.request_count,
            "member_count": len(self._zip.infolist()),
        }

    def close(self) -> None:
        self._zip.close()
        self._file.close()

    def __enter__(self) -> RemoteZip:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()