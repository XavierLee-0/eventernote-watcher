"""轮询调度 + 新活动检测与通知（核心业务，不依赖 Web 框架）。"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta

import httpx

from .db import Database
from .fetcher import fetch_actor_events
from .notifiers import build_all

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_schedule(sched) -> list[tuple[int, int]]:
    """解析 "01:00,09:00,17:00" 为 [(h, m), ...], 忽略非法片段。"""
    times = []
    if not sched:
        return times
    for part in str(sched).split(","):
        m = _TIME_RE.match(part.strip())
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if h < 24 and mi < 60:
                times.append((h, mi))
    return sorted(set(times))


class Watcher:
    def __init__(self, db: Database):
        self.db = db
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._running = False
        self._last_run_at: str | None = None
        self._next_run_at: str | None = None

    # ---------- 生命周期 ----------
    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def trigger_now(self):
        """WebUI 点“立即抓取”时唤醒轮询循环。"""
        self._wake.set()

    # ---------- 轮询 ----------
    def _next_delay(self, settings: dict) -> float:
        """计算到下次抓取的秒数。

        定点模式(poll_schedule 非空): 睡到下一个预定时刻(精确, 无抖动);
        间隔模式(回退): poll_interval_hours 小时 ±10%。
        """
        times = _parse_schedule(settings.get("poll_schedule"))
        if times:
            now = datetime.now()
            candidates = []
            for h, mi in times:
                t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                if t <= now:
                    t += timedelta(days=1)
                candidates.append(t)
            nxt = min(candidates)
            self._next_run_at = nxt.isoformat(timespec="minutes")
            return (nxt - now).total_seconds()
        hours = float(settings.get("poll_interval_hours", 8))
        self._next_run_at = None
        return hours * 3600 * random.uniform(0.9, 1.1)

    async def _loop(self):
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("watch cycle failed")
            await self._sleep_until_next()

    async def _sleep_until_next(self):
        """睡到下次抓取时刻。分段睡眠(最长15分钟一段), 每段醒来重读设置,
        让 WebUI 修改定点/间隔配置即时生效; 期间可被 trigger_now 提前唤醒。"""
        CHUNK = 900
        while True:
            delay = self._next_delay(self.db.get_settings())
            if delay <= 1:
                return
            step = min(delay, CHUNK)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=step)
                return  # 手动触发
            except asyncio.TimeoutError:
                if delay <= CHUNK + 1:
                    return  # 已到点
                # 未到点但一段结束: 继续循环, 顺便让设置变更生效

    # ---------- 单轮 ----------
    async def run_once(self) -> dict:
        """抓取所有启用的出演者，新活动合并通知。返回摘要。"""
        self._running = True
        summary = {"checked": 0, "failed": 0, "new_events": 0, "notified": False}
        try:
            actors = [a for a in self.db.list_actors() if a["enabled"]]
            self._last_run_at = datetime.now().isoformat(timespec="seconds")
            settings = self.db.get_settings()
            lo, hi = settings.get("fetch_delay_seconds", [3, 8])
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"}, timeout=30, follow_redirects=True
            ) as client:
                new_items: list[tuple[dict, list]] = []  # (actor, [Event])
                for i, actor in enumerate(actors):
                    if i > 0:
                        await asyncio.sleep(random.uniform(lo, hi))
                    try:
                        events = await fetch_actor_events(client, actor["actor_id"], actor["name"])
                        summary["checked"] += 1
                    except Exception as e:
                        summary["failed"] += 1
                        self.db.record_fetch(actor["actor_id"], False, str(e))
                        log.warning("fetch %s failed: %s", actor["name"], e)
                        continue
                    self.db.record_fetch(actor["actor_id"], True, None)
                    seen = self.db.get_seen_ids(actor["actor_id"])
                    fresh = [ev for ev in events if ev.event_id not in seen]
                    self.db.mark_seen(actor["actor_id"], events)
                    if actor["baselined"] and fresh:
                        new_items.append((actor, fresh))
                    elif not actor["baselined"]:
                        # 首次抓取：只建基线
                        self.db.update_actor(actor["actor_id"], baselined=True)
                        log.info("baseline for %s: %d events", actor["name"], len(events))
            if new_items:
                summary["new_events"] = sum(len(f) for _, f in new_items)
                summary["notified"] = await self._notify(new_items)
            return summary
        finally:
            self._running = False

    async def simulate_notify(self) -> dict:
        """用已抓取的真实活动数据走一遍完整通知流程(与真实新活动同一代码路径)。

        用于验证通知内容与格式, 标题带 [测试] 前缀, 不影响去重状态。
        """
        actors = [a for a in self.db.list_actors() if a["enabled"]]
        new_items: list[tuple[dict, list]] = []
        for actor in actors:
            events = self.db.list_events(actor["actor_id"], future_only=True)
            if events:
                # 取最近 10 场(不足则全部), 用 Event 结构还原
                from .fetcher import Event

                new_items.append((actor, [
                    Event(
                        event_id=ev["event_id"], name=ev["name"], date=ev["date"],
                        place=ev["place"], open_time=ev.get("open_time", ""),
                    )
                    for ev in events[:10]
                ]))
        if not new_items:
            return {"ok": False, "error": "没有可用的活动数据, 请先抓取一次"}
        ok = await self._notify(new_items, test=True)
        return {"ok": ok, "actors": len(new_items)}

    async def _notify(self, new_items: list[tuple[dict, list]], test: bool = False) -> bool:
        total = sum(len(f) for _, f in new_items)
        title = f"Eventernote: {len(new_items)} 位出演者有 {total} 场新活动"
        if test:
            title = "[测试] " + title
        lines = []
        for actor, fresh in new_items:
            lines.append(f"## {actor['name']}")
            for ev in fresh:
                when = ev.date or "日期未定"
                place = f" @ {ev.place}" if ev.place else ""
                lines.append(f"- [{ev.name}]({ev.url()})  \n  {when}{place}")
            lines.append("")
        content = "\n".join(lines)

        notifiers = build_all(self.db.get_settings().get("notifiers", {}))
        if not notifiers:
            log.warning("new events but no notifier enabled/configured")
            self.db.log_notification(None, None, "(none)", title, False, "无可用通知渠道")
            return False
        ok_any = False
        for n in notifiers:
            try:
                await n.send(title, content)
                self.db.log_notification(None, None, n.name, title, True)
                ok_any = True
            except Exception as e:
                log.error("notify via %s failed: %s", n.name, e)
                self.db.log_notification(None, None, n.name, title, False, str(e))
        return ok_any

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_run_at(self) -> str | None:
        return self._next_run_at

    @property
    def last_run_at(self) -> str | None:
        return self._last_run_at
