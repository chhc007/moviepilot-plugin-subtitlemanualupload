#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海拉鲁「批量匹配字幕」本地验证（stub 宿主依赖）。

覆盖：
 1) entry_from_target 字段映射（label → target_label、path 空 → None、识别字段透传）
 2) target_from_entry / entry_from_target 往返一致性
 3) POST /auto_transfer_queue/enqueue 端点：入参校验、entries 合并、入队、message/queued/skipped
 4) 路由注册包含 /auto_transfer_queue/enqueue
"""
import asyncio
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PARENT = os.path.abspath(os.path.join(HERE, "..", "plugins"))
sys.path.insert(0, PLUGIN_PARENT)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))


# ---------------------------------------------------------------- stubs
def install_stubs():
    def mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    app = mod("app")
    core = mod("app.core")
    app.core = core
    cfg = mod("app.core.config")

    class _Settings:
        PROXY = None
        TMDB_IMAGE_DOMAIN = "image.tmdb.org"
        RMT_MEDIAEXT = {".mkv", ".mp4", ".strm"}

    cfg.settings = _Settings()
    core.config = cfg

    log = mod("app.log")

    class _L:
        def _p(self, *a, **k):
            pass
        info = warning = error = debug = _p

    log.logger = _L()
    app.log = log

    metainfo = mod("app.core.metainfo")

    class _MetaInfoPath:
        def __init__(self, *a, **k):
            pass

    metainfo.MetaInfoPath = _MetaInfoPath
    core.metainfo = metainfo

    db = mod("app.db")
    models = mod("app.db.models")
    th = mod("app.db.models.transferhistory")

    class _TransferHistory:
        pass

    th.TransferHistory = _TransferHistory
    models.transferhistory = th
    db.models = models
    app.db = db

    plugins = mod("app.plugins")

    class _PluginBase:
        pass

    plugins._PluginBase = _PluginBase
    app.plugins = plugins

    core_plugin = mod("app.core.plugin")

    class _PluginManager:
        _instance = None

        def __new__(cls):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.running_plugins = {}
            return cls._instance

    core_plugin.PluginManager = _PluginManager
    core.plugin = core_plugin

    schemas = mod("app.schemas")
    stypes = mod("app.schemas.types")

    class _MediaType:
        MOVIE = "movie"
        TV = "tv"

    stypes.MediaType = _MediaType

    class _EventType:
        TransferComplete = "transfer.complete"

    stypes.EventType = _EventType
    schemas.types = stypes
    app.schemas = schemas

    chain = mod("app.chain")
    tmdb = mod("app.chain.tmdb")

    class _TmdbChain:
        pass

    tmdb.TmdbChain = _TmdbChain
    chain.tmdb = tmdb
    app.chain = chain


install_stubs()

from fastapi import HTTPException  # noqa: E402
import subtitlemanualupload.catalog.media_target_resolver as resolver_mod  # noqa: E402
from subtitlemanualupload.catalog.media_target_resolver import MediaTargetResolver  # noqa: E402
from subtitlemanualupload.api.status_api import StatusApi  # noqa: E402
from subtitlemanualupload.api.routes import build_api_routes  # noqa: E402


def make_resolver():
    return MediaTargetResolver(
        settings_obj=types.SimpleNamespace(RMT_MEDIAEXT={".mkv", ".mp4", ".strm"}),
        meta_info_path=None,
        stream_exts={".strm"},
        trust_transfer_history_paths=False,
        normalize_text=lambda v: str(v or "").strip(),
        safe_int=lambda v, d=0: (int(v) if str(v or "").strip().lstrip("-").isdigit() else d),
        hash_text=lambda s: "h" + str(abs(hash(s)) % 10**12),
        extract_episode_hint=lambda name: None,
        subtitle_files_provider=lambda entry: [],
        subtitle_files_batch_provider=None,
        load_local_entries=lambda **k: [],
        group_entries_as_media=lambda entries, limit: [],
        tmdb_detail_for_media=lambda media: {},
        apply_tmdb_detail=lambda target, detail: None,
        target_entry_cache=types.SimpleNamespace(remember=lambda entries: None),
    )


print("=" * 72)
print("1) entry_from_target 字段映射")
resolver = make_resolver()
target = {
    "id": "t1",
    "label": "S01E01 · Show.S01E01.mkv",
    "basename": "Show.S01E01",
    "path": "/media/Show/Show.S01E01.mkv",
    "media_type": "tv",
    "title": "Show",
    "tmdb_id": 12345,
    "douban_id": "",
    "season": 1,
    "episode": 1,
    "year": "2020",
    "library_name": "MoviePilot 媒体库",
    "relative_path": "/media/Show/Show.S01E01.mkv",
    "storage": "local",
    "writable": True,
    "original_language": "ja",
    "origin_country": ["JP"],
    "production_countries": [{"iso_3166_1": "JP"}],
    "original_title": "ショウ",
    "original_name": "ショウ",
    "en_title": "Show",
    "tmdb_aliases": ["Show Alias"],
    "subtitles": [{"name": "x.ass"}],
}
entry = resolver.entry_from_target(target)
check("path 有值返回 entry", isinstance(entry, dict))
check("label → target_label 映射", entry["target_label"] == target["label"], str(entry.get("target_label")))
check("path 保留", entry["path"] == target["path"])
check("filename 从 path 推导", entry["filename"] == "Show.S01E01.mkv", str(entry.get("filename")))
check("basename 保留", entry["basename"] == "Show.S01E01")
check("media_type 保留", entry["media_type"] == "tv")
check("season/episode 保留", entry["season"] == 1 and entry["episode"] == 1)
check("tmdb_id 保留", entry["tmdb_id"] == 12345)
check("识别字段透传 original_language", entry["original_language"] == "ja")
check("识别字段透传 tmdb_aliases", entry["tmdb_aliases"] == ["Show Alias"])
check("识别字段透传 production_countries", entry["production_countries"] == [{"iso_3166_1": "JP"}])
check("subtitles 展示字段不透传", "subtitles" not in entry)
check("media_key 已生成", bool(entry.get("media_key")))

check("path 为空返回 None", resolver.entry_from_target({"id": "x", "path": ""}) is None)
check("非 dict 返回 None", resolver.entry_from_target(None) is None)

print()
print("2) target_from_entry / entry_from_target 往返")
back = resolver.target_from_entry(entry, subtitles=[])
check("往返 label 一致", back["label"] == target["label"])
check("往返 path 一致", back["path"] == target["path"])
check("往返 season/episode 一致", back["season"] == 1 and back["episode"] == 1)
roundtrip = resolver.entry_from_target(back)
check("二次转换 target_label 仍正确", roundtrip["target_label"] == target["label"], str(roundtrip.get("target_label")))
check("二次转换 media_key 稳定", roundtrip["media_key"] == entry["media_key"])

print()
print("3) POST /auto_transfer_queue/enqueue 端点")


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class FakeCatalog:
    def __init__(self):
        self.merged = None

    def merge_local_entries_cache(self, entries):
        self.merged = entries


class FakeAutoTransfer:
    def __init__(self):
        self.enqueued = None

    def enqueue_transfer_auto_entries(self, entries):
        self.enqueued = entries
        return len(entries), 1

    def auto_transfer_queue_snapshot(self, limit=100):
        return {"summary": {"total": 2, "pending": 1}, "tasks": [], "rate_limits": {}, "season_package_cache": []}


class FakeServices:
    def __init__(self, resolver, catalog, auto_transfer):
        self._resolver = resolver
        self._catalog = catalog
        self._auto_transfer = auto_transfer

    def target_resolver(self):
        return self._resolver

    def local_media_catalog(self):
        return self._catalog

    def auto_transfer(self):
        return self._auto_transfer


class FakeOwner:
    def __init__(self, services):
        self.services = services

    def _ok(self, data=None, message="ok"):
        return {"success": True, "message": message, "data": data}


catalog = FakeCatalog()
auto_transfer = FakeAutoTransfer()
owner = FakeOwner(FakeServices(resolver, catalog, auto_transfer))
api = StatusApi(owner)

resp = asyncio.run(api.enqueue_auto_transfer_targets(FakeRequest({"targets": [target]})))
check("返回 success", resp.get("success") is True)
check("data 含 queued=1", resp["data"].get("queued") == 1, str(resp["data"].get("queued")))
check("data 含 skipped=1", resp["data"].get("skipped") == 1)
check("data 含队列快照 summary", resp["data"].get("summary") == {"total": 2, "pending": 1})
check("message 文案含提交数", resp["message"] == "已提交 1 个目标到自动处理队列，跳过 1 个", resp["message"])
check("entries 已合并到本地缓存", catalog.merged is not None and len(catalog.merged) == 1)
check("entries 已入队", auto_transfer.enqueued is not None and auto_transfer.enqueued[0]["target_label"] == target["label"])

# skipped=0 时 message 不带跳过
class ZeroSkipAutoTransfer(FakeAutoTransfer):
    def enqueue_transfer_auto_entries(self, entries):
        self.enqueued = entries
        return len(entries), 0


owner2 = FakeOwner(FakeServices(resolver, FakeCatalog(), ZeroSkipAutoTransfer()))
resp2 = asyncio.run(StatusApi(owner2).enqueue_auto_transfer_targets(FakeRequest({"targets": [target]})))
check("skipped=0 时 message 不含跳过", resp2["message"] == "已提交 1 个目标到自动处理队列", resp2["message"])

# 缺少 targets
try:
    asyncio.run(api.enqueue_auto_transfer_targets(FakeRequest({})))
    check("缺少 targets 抛 400", False, "未抛异常")
except HTTPException as exc:
    check("缺少 targets 抛 400", exc.status_code == 400, str(exc.status_code))

try:
    asyncio.run(api.enqueue_auto_transfer_targets(FakeRequest({"targets": []})))
    check("空 targets 抛 400", False, "未抛异常")
except HTTPException as exc:
    check("空 targets 抛 400", exc.status_code == 400)

# 全部无效 target（path 为空）
try:
    asyncio.run(api.enqueue_auto_transfer_targets(FakeRequest({"targets": [{"id": "x", "path": ""}]})))
    check("无有效目标抛 400", False, "未抛异常")
except HTTPException as exc:
    check("无有效目标抛 400", exc.status_code == 400)

print()
print("4) 路由注册")
routes = build_api_routes(owner)
paths = {(r["path"], tuple(r["methods"])) for r in routes}
check("/auto_transfer_queue/enqueue 已注册 POST", ("/auto_transfer_queue/enqueue", ("POST",)) in paths)
enqueue_route = next((r for r in routes if r["path"] == "/auto_transfer_queue/enqueue"), None)
check("路由 auth=bear", enqueue_route and enqueue_route["auth"] == "bear")
check("路由 endpoint 指向新方法", enqueue_route and enqueue_route["endpoint"].__name__ == "enqueue_auto_transfer_targets")

print()
print("5) 真实 AutoTransferQueue 消费 entry_from_target 产物")
import tempfile  # noqa: E402
import time as time_mod  # noqa: E402
from collections import OrderedDict  # noqa: E402
from pathlib import Path as _Path  # noqa: E402
from subtitlemanualupload.auto_transfer.auto_transfer_queue import AutoTransferQueue  # noqa: E402
from subtitlemanualupload.catalog.target_normalizers import (  # noqa: E402
    entry_filesystem_signature,
    entry_path_is_valid,
)

tmp_dir = _Path(tempfile.mkdtemp(prefix="batch_match_"))
video = tmp_dir / "Show.S01E01.mkv"
video.write_bytes(b"video")


class QueueOwner:
    def __init__(self):
        self._transfer_auto_lock = __import__("threading").Lock()
        self._transfer_auto_recent = {}
        self._transfer_auto_dedupe_seconds = 300
        self._auto_transfer_tasks = OrderedDict()
        self._auto_transfer_queue_history_limit = 200
        self._auto_transfer_stopping = False
        self._auto_transfer_worker = None
        self._auto_season_package_cache = OrderedDict()
        self._auto_transfer_queue_debounce_seconds = 3
        self.services = types.SimpleNamespace(
            local_media_catalog=lambda: types.SimpleNamespace(
                filter_existing_local_entries=lambda entries: [
                    e for e in entries if entry_path_is_valid(
                        e, normalize_text=lambda v: str(v or "").strip()
                    )
                ]
            )
        )

    def _normalize_text(self, value):
        return str(value or "").strip()

    def _safe_int(self, value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    def _hash_text(self, value):
        return "h" + str(abs(hash(value)) % 10**12)

    def _entry_filesystem_signature(self, entry):
        return entry_filesystem_signature(entry, normalize_text=lambda v: str(v or "").strip())

    def _json_clone(self, value):
        import json
        return json.loads(json.dumps(value))

    def _timestamp_iso(self, value):
        return str(value)


queue_owner = QueueOwner()
queue = AutoTransferQueue(queue_owner, time_module=time_mod)
real_target = dict(target, path=str(video), relative_path=str(video), basename=video.stem)
real_entry = resolver.entry_from_target(real_target)
queued, skipped = queue.enqueue_entries([real_entry])
check("真实队列入队成功 queued=1", queued == 1 and skipped == 0, f"queued={queued} skipped={skipped}")
task = next(iter(queue_owner._auto_transfer_tasks.values()))
check("任务 target_label 来自 label", task["target_label"] == real_target["label"])
check("任务 entry 保留 path", task["entry"]["path"] == str(video))
check("任务 season/episode 正确", task["season"] == 1 and task["episode"] == 1)
queued2, skipped2 = queue.enqueue_entries([real_entry])
check("重复入队被去重", queued2 == 0 and skipped2 == 1, f"queued={queued2} skipped={skipped2}")
summary = queue.summary()
check("队列 summary 统计到 1 条", summary["total"] == 1, str(summary))

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
