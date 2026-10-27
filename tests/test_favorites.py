import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.api import create_router
from app.db import Database
from app.favorites import (FavoriteActor, ProfileNotFound, fetch_user_favorites,
                           normalize_username, parse_favorites)
from app.fetcher import Event, FetchError


# Minimal fixture retaining the observed public profile/sidebar structure.
PROFILE = """
<main><div class="actor"><a href="/actors/Unfollowed/999">Unfollowed guest</a></div></main>
<div class="span4 page">
  <div class="gb_users_side_profile"><h2 class="name1">example_user</h2></div>
  <h2 class="gb_subtitle">お気に入り声優/アーティスト</h2>
  <div><ul class="gb_actors_list unstyled">
    <li><a href="/actors/fh%C3%A1na/7025">fhána</a></li>
    <li><a href="/actors/Roselia/24401">Roselia</a></li>
    <li><a href="/actors/Roselia/24401">Roselia</a></li>
  </ul></div>
  <h2>イベント参加数カレンダー</h2>
  <div><a href="/actors/Unfollowed/998">Another guest</a></div>
</div>
<footer><a href="/actors/Popular/28">Popular artist</a></footer>
"""
EMPTY_PROFILE = """
<div class="span4 page"><div class="gb_users_side_profile"></div>
<h2 class="gb_subtitle">お気に入り声優/アーティスト</h2>
<h2>イベント参加数カレンダー</h2></div>
"""


class FavoritesParserTests(unittest.TestCase):
    def test_accepts_username_and_profile_links(self):
        for value in (" example_user ", "@example_user",
                      "https://www.eventernote.com/users/example_user/",
                      "http://eventernote.com/users/example_user?ref=profile#top",
                      "www.eventernote.com/users/example_user"):
            with self.subTest(value=value):
                self.assertEqual(normalize_username(value), "example_user")

    def test_rejects_other_hosts_credentials_and_nonprofile_paths(self):
        for value in ("", "../../admin", "https://127.0.0.1/users/example_user",
                      "https://www.eventernote.com.evil.test/users/example_user",
                      "https://evil.test@www.eventernote.com/users/example_user",
                      "https://www.eventernote.com:8080/users/example_user",
                      "https://www.eventernote.com/users/example_user/following",
                      "https://www.eventernote.com/users/example_user%2Fother"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_username(value)

    def test_reads_only_favorites_deduplicating_by_id(self):
        actors = parse_favorites(PROFILE)
        self.assertEqual([(a.actor_id, a.name) for a in actors],
                         [(7025, "fhána"), (24401, "Roselia")])

    def test_empty_profile_is_valid(self):
        self.assertEqual(parse_favorites(EMPTY_PROFILE), [])

    def test_unrecognized_page_or_broken_actor_link_fails(self):
        for html in ("<html>Login required</html>",
                     PROFILE.replace("/actors/Roselia/24401", "/actors/broken"),
                     PROFILE.replace("gb_actors_list", "new_list_class")):
            with self.subTest(html=html[:40]), self.assertRaises(FetchError):
                parse_favorites(html)


class FavoritesFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_only_fixed_public_profile_url(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(200, text=PROFILE)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(len(await fetch_user_favorites(client, "example_user")), 2)
        self.assertEqual(requests, ["https://www.eventernote.com/users/example_user"])

    async def test_missing_profile_and_redirect_do_not_import(self):
        for status, error in ((404, ProfileNotFound), (302, FetchError), (429, FetchError)):
            with self.subTest(status=status):
                transport = httpx.MockTransport(lambda request: httpx.Response(
                    status, headers={"location": "https://other.test/login"}))
                async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
                    with self.assertRaises(error):
                        await fetch_user_favorites(client, "example_user")


class FavoritesImportApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(Path(self.directory.name) / "test.db")
        self.db.add_actor(7025, "Existing custom name")
        self.db.update_actor(7025, enabled=False, baselined=True)
        self.db.mark_seen(7025, [Event(7, "Saved concert", "2026-10-27", "Venue", "18:00")])
        app = FastAPI()
        app.include_router(create_router(self.db, object()))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_appends_and_repeat_import_preserves_existing_state(self):
        favorites = [FavoriteActor(7025, "fhána"), FavoriteActor(24401, "Roselia")]
        with patch("app.api.fetch_user_favorites", new=AsyncMock(return_value=favorites)) as fetch:
            first = await self.client.post("/api/actors/import-favorites", json={
                "user": "https://www.eventernote.com/users/example_user"})
            second = await self.client.post("/api/actors/import-favorites", json={"user": "example_user"})
            self.assertEqual(fetch.call_args.args[1], "example_user")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"ok": True, "username": "example_user", "total": 2, "added": 1, "skipped": 1})
        self.assertEqual(second.json()["added"], 0)
        self.assertEqual(second.json()["skipped"], 2)
        existing = next(a for a in self.db.list_actors() if a["actor_id"] == 7025)
        self.assertEqual((existing["name"], existing["enabled"], existing["baselined"]),
                         ("Existing custom name", 0, 1))
        self.assertEqual(len(self.db.list_events()), 1)
        self.assertEqual(self.db.get_settings()["poll_schedule"], "01:00,09:00,17:00")

    async def test_bad_input_does_not_fetch(self):
        with patch("app.api.fetch_user_favorites", new=AsyncMock()) as fetch:
            response = await self.client.post("/api/actors/import-favorites", json={"user": "https://other.test/users/example_user"})
            self.assertEqual(response.status_code, 422)
            fetch.assert_not_awaited()

    async def test_fetch_errors_leave_database_unchanged(self):
        errors = [(ProfileNotFound("missing"), 404), (FetchError("blocked"), 502),
                  (httpx.ConnectTimeout("timeout"), 502)]
        for error, status in errors:
            with self.subTest(error=error), patch("app.api.fetch_user_favorites", new=AsyncMock(side_effect=error)):
                response = await self.client.post("/api/actors/import-favorites", json={"user": "example_user"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(len(self.db.list_actors()), 1)

    async def test_empty_favorites_keeps_existing_actors(self):
        with patch("app.api.fetch_user_favorites", new=AsyncMock(return_value=[])):
            response = await self.client.post("/api/actors/import-favorites", json={"user": "example_user"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)
        self.assertEqual(len(self.db.list_actors()), 1)


if __name__ == "__main__":
    unittest.main()
