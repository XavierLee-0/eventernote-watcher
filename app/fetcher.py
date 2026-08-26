"""抓取并解析 Eventernote 公开页面。

活动列表: GET /actors/{name}/{id}/events?limit=30&page=N  (HTML)
出演者搜索: GET /api/actors/search?keyword=...&page=N       (JSON, 非官方 API)
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://www.eventernote.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_PAGES = 5  # 单个出演者最多翻页数（按 event_date 降序，未来活动集中在最前）

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class Event:
    event_id: int
    name: str
    date: str  # YYYY-MM-DD 或空串
    place: str
    open_time: str  # 開場/開演 等时间信息原文

    def url(self) -> str:
        return f"{BASE_URL}/events/{self.event_id}"


@dataclass
class ActorSearchResult:
    actor_id: int
    name: str
    kana: str
    favorite_count: int


class FetchError(Exception):
    pass


async def fetch_actor_events(
    client: httpx.AsyncClient, actor_id: int, name_for_url: str = ""
) -> list[Event]:
    """抓取某出演者的活动列表（未来活动），翻页直到出现过期活动或达上限。"""
    slug = quote(name_for_url, safe="") if name_for_url else str(actor_id)
    events: list[Event] = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/actors/{slug}/{actor_id}/events"
        resp = await client.get(url, params={"actor_id": actor_id, "limit": 30, "page": page})
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.gb_event_list li.clearfix")
        if not items:
            break
        has_past = False
        for li in items:
            ev = _parse_event_li(li)
            if ev is None:
                continue
            events.append(ev)
            if ev.date and ev.date < _today():
                has_past = True
        if has_past or len(items) < 30:
            break
        await asyncio.sleep(2)  # 翻页间隔
    return events


def _parse_event_li(li) -> Event | None:
    a = li.select_one(".event h4 a")
    if not a or not a.get("href"):
        return None
    m = re.search(r"/events/(\d+)", a["href"])
    if not m:
        return None
    event_id = int(m.group(1))
    date = ""
    date_p = li.select_one(".date p")
    if date_p:
        m2 = _DATE_RE.search(date_p.get_text())
        if m2:
            date = m2.group(1)
    place_a = li.select_one(".event .place a")
    place = place_a.get_text(strip=True) if place_a else ""
    s = li.select_one(".event .place .s")
    open_time = s.get_text(" ", strip=True) if s else ""
    return Event(event_id, a.get_text(strip=True), date, place, open_time)


async def search_actors(
    client: httpx.AsyncClient, keyword: str, page: int = 1
) -> list[ActorSearchResult]:
    """调用站点的 JSON API 搜索出演者。"""
    resp = await client.get(
        f"{BASE_URL}/api/actors/search",
        params={"keyword": keyword, "page": page},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    if resp.status_code != 200:
        raise FetchError(f"actor search HTTP {resp.status_code}")
    data = resp.json()
    results = []
    for r in data.get("results", []):
        if r.get("delete_flag"):
            continue
        results.append(
            ActorSearchResult(
                actor_id=r["id"],
                name=r.get("name", ""),
                kana=r.get("kana", ""),
                favorite_count=r.get("favorite_count", 0),
            )
        )
    return results


def _today() -> str:
    from datetime import date

    return date.today().isoformat()
