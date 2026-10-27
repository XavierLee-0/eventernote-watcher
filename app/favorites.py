"""从公开用户主页读取收藏艺人，不读取活动中的出演者或关注用户。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .fetcher import BASE_URL, FetchError

_USERNAME = re.compile(r"[A-Za-z0-9_]{1,64}")
_HOSTS = {"eventernote.com", "www.eventernote.com"}


class ProfileNotFound(FetchError):
    pass


@dataclass
class FavoriteActor:
    actor_id: int
    name: str


def normalize_username(value: str) -> str:
    value = value.strip()
    if _USERNAME.fullmatch(value.removeprefix("@")):
        return value.removeprefix("@")
    if value.startswith(("eventernote.com/", "www.eventernote.com/")):
        value = "https://" + value
    parsed = urlsplit(value)
    match = re.fullmatch(r"/users/([A-Za-z0-9_]{1,64})/?", parsed.path)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() in _HOSTS and match:
        return match[1]
    raise ValueError("请输入 Eventernote 用户名或个人主页链接，例如 https://www.eventernote.com/users/用户名")


def parse_favorites(html: str) -> list[FavoriteActor]:
    soup = BeautifulSoup(html, "html.parser")
    profile = soup.select_one(".gb_users_side_profile")
    sidebar = profile.find_parent("div", class_="page") if profile else None
    heading = next((h for h in sidebar.find_all(["h2", "h3"])
                    if "お気に入り声優" in h.get_text()), None) if sidebar else None
    if heading is None:
        raise FetchError("未能识别公开个人主页，请确认该用户主页可公开访问，或稍后重试")

    actors: dict[int, FavoriteActor] = {}
    for sibling in heading.next_siblings:
        if getattr(sibling, "name", None) in {"h2", "h3"}:
            break
        if not getattr(sibling, "select", None):
            continue
        if sibling.select('a[href^="/actors/"]') and not sibling.select("ul.gb_actors_list"):
            raise FetchError("关注艺人列表格式发生变化，暂时无法导入")
        for link in sibling.select("ul.gb_actors_list a[href]"):
            try:
                target = urlsplit(link["href"])
            except ValueError as exc:
                raise FetchError("关注艺人列表包含无法识别的链接，请稍后重试") from exc
            if target.netloc and target.netloc.lower() not in _HOSTS:
                raise FetchError("关注艺人列表包含无法识别的链接，请稍后重试")
            match = re.fullmatch(r"/actors/[^/]+/(\d+)/?", target.path)
            name = link.get_text(" ", strip=True)
            if not match or int(match[1]) <= 0 or not name:
                raise FetchError("关注艺人列表格式发生变化，暂时无法导入")
            actor_id = int(match[1])
            actors.setdefault(actor_id, FavoriteActor(actor_id, name))
    return list(actors.values())


async def fetch_user_favorites(client: httpx.AsyncClient, username: str) -> list[FavoriteActor]:
    username = normalize_username(username)
    response = await client.get(f"{BASE_URL}/users/{username}", follow_redirects=False)
    if response.status_code == 404:
        raise ProfileNotFound("找不到该 Eventernote 用户，请检查用户名")
    if response.status_code != 200:
        raise FetchError(f"无法读取公开个人主页（HTTP {response.status_code}），请稍后重试")
    return parse_favorites(response.text)
