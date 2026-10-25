"""HTTP-клиент только для заранее разрешённых имён. Чужой адрес и частная сеть отсекаются."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from parser import __version__

REDIRECTS = {301, 302, 303, 307, 308}
Resolver = Callable[[str], list[str]]


class Fetcher(Protocol):
    def get(self, url: str, allowed_hosts: frozenset[str]) -> tuple[bytes, str]: ...


class FetchDenied(Exception):
    """Адрес нельзя запрашивать."""


class FetchFailed(Exception):
    """Источник ответил ошибкой или оборвал соединение."""


def default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchDenied("имя не разрешилось") from exc
    addresses = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise FetchDenied("имя не разрешилось")
    return addresses


def host_allowed(host: str, allowed_hosts: frozenset[str]) -> bool:
    name = host.lower().rstrip(".")
    apex = name[4:] if name.startswith("www.") else name
    return apex in allowed_hosts or name in allowed_hosts


def check_url(url: str, allowed_hosts: frozenset[str], resolver: Resolver) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https":
        raise FetchDenied("разрешён только https")
    if parts.username or parts.password:
        raise FetchDenied("логин в адресе запрещён")
    host = parts.hostname
    if not host:
        raise FetchDenied("нет имени хоста")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise FetchDenied("числовой адрес запрещён")
    if host.replace(".", "").isdigit():
        raise FetchDenied("числовой адрес запрещён")
    if not host_allowed(host, allowed_hosts):
        raise FetchDenied(f"хост {host} не в списке источника")
    for raw_address in resolver(host):
        address = ipaddress.ip_address(raw_address)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global:
            raise FetchDenied("адрес источника не является публичным")
    return url


class HttpFetcher:
    def __init__(
        self,
        client: httpx.Client,
        resolver: Resolver = default_resolver,
        max_bytes: int = 1_000_000,
        max_redirects: int = 2,
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    def get(self, url: str, allowed_hosts: frozenset[str]) -> tuple[bytes, str]:
        current = check_url(url, allowed_hosts, self._resolver)
        for _ in range(self._max_redirects + 1):
            try:
                with self._client.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailed("редирект без адреса")
                        current = check_url(urljoin(current, location), allowed_hosts, self._resolver)
                        continue
                    if response.status_code >= 400:
                        raise FetchFailed(f"HTTP {response.status_code}")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._max_bytes:
                            raise FetchDenied("ответ больше лимита")
                        chunks.append(chunk)
                    return b"".join(chunks), str(response.url)
            except httpx.HTTPError as exc:
                raise FetchFailed("соединение прервано") from exc
        raise FetchDenied("слишком много редиректов")


class OfflineFetcher:
    def get(self, url: str, allowed_hosts: frozenset[str]) -> tuple[bytes, str]:
        raise FetchDenied("офлайн-режим не открывает сеть")


def make_fetcher(offline: bool, timeout: float, max_bytes: int) -> Fetcher:
    if offline:
        return OfflineFetcher()
    return HttpFetcher(build_client(timeout), max_bytes=max_bytes)


def build_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=5.0),
        follow_redirects=False,
        headers={"User-Agent": f"INSIGHT-parser/{__version__}"},
    )
