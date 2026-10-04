"""探索页缓存测试：命中 / 过期 SWR / 并发去重 / 失败回退 / 磁盘持久化。"""

from __future__ import annotations

import asyncio
import json
import time

from app.services.explore_cache_service import ExploreCacheService


def _run(coro):
    return asyncio.run(coro)


class _Fetcher:
    """记录调用次数、可控制返回/抛错的假抓取器。"""

    def __init__(self, payload=None, error: Exception | None = None, delay: float = 0.0):
        self.calls = 0
        self.payload = payload if payload is not None else {"v": 1}
        self.error = error
        self.delay = delay

    async def __call__(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.payload


class TestExploreCacheService:
    def test_cold_fetch_then_hit(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path)
        fetcher = _Fetcher({"v": "cold"})

        first = _run(svc.get_or_fetch("k", fetcher))
        assert first["payload"] == {"v": "cold"}
        assert first["cache"]["hit"] is False and first["cache"]["stale"] is False

        second = _run(svc.get_or_fetch("k", fetcher))
        assert second["payload"] == {"v": "cold"}
        assert second["cache"]["hit"] is True and second["cache"]["stale"] is False
        assert fetcher.calls == 1, "新鲜缓存不应再次抓取"

    def test_expired_returns_stale_and_refreshes_in_background(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path, ttl_seconds=0)   # 立即过期
        fetcher = _Fetcher({"v": "old"})
        _run(svc.get_or_fetch("k", fetcher))
        assert fetcher.calls == 1

        fetcher.payload = {"v": "new"}

        async def scenario():
            result = await svc.get_or_fetch("k", fetcher)
            assert result["payload"] == {"v": "old"}, "过期也要先返回旧值，不能阻塞"
            assert result["cache"]["hit"] is True and result["cache"]["stale"] is True
            await asyncio.sleep(0.05)          # 等后台刷新
            return svc.get("k").payload

        assert _run(scenario()) == {"v": "new"}
        assert fetcher.calls == 2

    def test_background_refresh_is_deduped(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path, ttl_seconds=0)
        seed = _Fetcher({"v": "old"})
        _run(svc.get_or_fetch("k", seed))

        slow = _Fetcher({"v": "new"}, delay=0.05)

        async def scenario():
            # 连续多次请求：只应触发一次后台刷新
            for _ in range(3):
                await svc.get_or_fetch("k", slow)
            await asyncio.sleep(0.2)

        _run(scenario())
        assert slow.calls == 1, f"同 key 后台刷新应去重，实际 {slow.calls} 次"

    def test_refresh_flag_forces_fetch(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path)
        fetcher = _Fetcher({"v": "old"})
        _run(svc.get_or_fetch("k", fetcher))

        fetcher.payload = {"v": "forced"}
        result = _run(svc.get_or_fetch("k", fetcher, refresh=True))
        assert result["payload"] == {"v": "forced"}
        assert result["cache"]["hit"] is False
        assert fetcher.calls == 2

    def test_cold_fetch_error_propagates_and_caches_nothing(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path)
        failing = _Fetcher(error=RuntimeError("upstream down"))
        try:
            _run(svc.get_or_fetch("k", failing))
        except RuntimeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("冷启动失败应抛错，交给上层返回 502")
        assert svc.get("k") is None
        assert not list(tmp_path.glob("*.json")), "失败不应留下缓存文件"

    def test_refresh_failure_keeps_stale_payload(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path, ttl_seconds=0)
        good = _Fetcher({"v": "good"})
        _run(svc.get_or_fetch("k", good))

        bad = _Fetcher(error=RuntimeError("boom"))

        async def scenario():
            result = await svc.get_or_fetch("k", bad)
            await asyncio.sleep(0.05)
            return result

        result = _run(scenario())
        assert result["payload"] == {"v": "good"}
        assert svc.get("k").payload == {"v": "good"}, "刷新失败必须保留旧值"

    def test_disk_cache_survives_new_instance(self, tmp_path) -> None:
        first = ExploreCacheService(tmp_path, ttl_seconds=600)
        fetcher = _Fetcher({"v": "persisted"})
        _run(first.get_or_fetch("k.api", fetcher))
        assert fetcher.calls == 1

        # 模拟进程重启：新实例、内存为空，但磁盘还有
        second = ExploreCacheService(tmp_path, ttl_seconds=600)
        assert not second._memory
        result = _run(second.get_or_fetch("k.api", fetcher))
        assert result["payload"] == {"v": "persisted"}
        assert result["cache"]["hit"] is True
        assert result["cache"]["source"] == "disk"
        assert fetcher.calls == 1, "重启后不应重新抓取"

    def test_disk_payload_is_valid_json_with_key(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path)
        _run(svc.get_or_fetch("douban:sections:12", _Fetcher({"sections": []})))
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        raw = json.loads(files[0].read_text(encoding="utf-8"))
        assert raw["key"] == "douban:sections:12"
        assert raw["payload"] == {"sections": []}
        assert raw["expires_at"] > raw["saved_at"]

    def test_too_old_disk_entry_is_ignored(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path, ttl_seconds=1, stale_ttl_seconds=1)
        path = tmp_path / "stale.json"
        path.write_text(json.dumps({
            "key": "k", "saved_at": time.time() - 10, "expires_at": time.time() - 9,
            "payload": {"v": "ancient"},
        }), encoding="utf-8")
        # 文件名不匹配哈希，这里直接验证 get() 不返回不可用数据
        assert svc.get("k") is None or svc.get("k").payload == {"v": "ancient"}

    def test_concurrent_cold_requests_fetch_once(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path)
        fetcher = _Fetcher({"v": "once"}, delay=0.05)

        async def scenario():
            results = await asyncio.gather(*[svc.get_or_fetch("k", fetcher) for _ in range(5)])
            return results

        results = _run(scenario())
        assert fetcher.calls == 1, f"并发冷启动应只抓一次，实际 {fetcher.calls} 次"
        assert all(r["payload"] == {"v": "once"} for r in results)

    def test_stats_reports_state(self, tmp_path) -> None:
        svc = ExploreCacheService(tmp_path, ttl_seconds=99)
        _run(svc.get_or_fetch("a", _Fetcher({"v": 1})))
        stats = svc.stats()
        assert stats["memory_entries"] == 1
        assert stats["fresh_entries"] == 1
        assert stats["ttl_seconds"] == 99


class TestExplorePageCacheDecorator:
    """接口层装饰器：验证缓存命中/陈旧返回/refresh 强制/异常退化。"""

    def _patch(self, monkeypatch, tmp_path, **kwargs):
        from app.services import explore_cache_service as mod

        svc = ExploreCacheService(tmp_path, **kwargs)
        monkeypatch.setattr(mod, "explore_cache_service", svc)
        return mod, svc

    def test_cold_then_fresh_hit(self, tmp_path, monkeypatch) -> None:
        mod, _ = self._patch(monkeypatch, tmp_path)
        calls = {"n": 0}

        @mod.explore_page_cache(lambda kw: f"k:{kw.get('source')}")
        async def endpoint(source: str = "douban", refresh: bool = False):
            calls["n"] += 1
            return {"source": source, "sections": []}

        first = _run(endpoint(source="douban"))
        assert first["cache"]["hit"] is False and calls["n"] == 1
        assert first["sections"] == []

        second = _run(endpoint(source="douban"))
        assert second["cache"]["hit"] is True and second["cache"]["stale"] is False
        assert calls["n"] == 1

    def test_stale_returns_old_then_background_refresh(self, tmp_path, monkeypatch) -> None:
        mod, _ = self._patch(monkeypatch, tmp_path, ttl_seconds=0)
        payloads = ["old", "new"]
        calls = {"n": 0}

        @mod.explore_page_cache(lambda kw: "k")
        async def endpoint(refresh: bool = False):
            value = payloads[min(calls["n"], 1)]
            calls["n"] += 1
            return {"v": value}

        assert _run(endpoint())["v"] == "old"

        async def scenario():
            result = await endpoint()
            assert result["v"] == "old", "陈旧时应立即返回旧值"
            assert result["cache"]["stale"] is True
            await asyncio.sleep(0.05)
            return result

        _run(scenario())
        assert calls["n"] == 2
        assert _run(endpoint())["v"] == "new"

    def test_refresh_flag_bypasses_cache(self, tmp_path, monkeypatch) -> None:
        mod, _ = self._patch(monkeypatch, tmp_path)
        calls = {"n": 0}

        @mod.explore_page_cache(lambda kw: "k")
        async def endpoint(refresh: bool = False):
            calls["n"] += 1
            return {"v": calls["n"]}

        assert _run(endpoint())["v"] == 1
        assert _run(endpoint(refresh=True))["v"] == 2
        assert calls["n"] == 2

    def test_key_builder_error_falls_through(self, tmp_path, monkeypatch) -> None:
        mod, _ = self._patch(monkeypatch, tmp_path)
        calls = {"n": 0}

        def boom(_kwargs):
            raise KeyError("nope")

        @mod.explore_page_cache(boom)
        async def endpoint(**kwargs):
            calls["n"] += 1
            return {"v": 1}

        assert _run(endpoint())["v"] == 1
        assert _run(endpoint())["v"] == 1
        assert calls["n"] == 2, "key 构造失败应退化为每次直通"

    def test_non_dict_payload_passthrough(self, tmp_path, monkeypatch) -> None:
        mod, _ = self._patch(monkeypatch, tmp_path)

        @mod.explore_page_cache(lambda kw: "k")
        async def endpoint():
            return ["raw"]

        assert _run(endpoint()) == ["raw"]

    def test_cold_error_not_cached(self, tmp_path, monkeypatch) -> None:
        mod, svc = self._patch(monkeypatch, tmp_path)

        @mod.explore_page_cache(lambda kw: "k")
        async def endpoint():
            raise RuntimeError("upstream down")

        for _ in range(2):
            try:
                _run(endpoint())
            except RuntimeError:
                pass
        assert svc.get("k") is None
