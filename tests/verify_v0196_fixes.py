#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海拉鲁 v0.1.96 修复本地验证（stub 宿主依赖）。

覆盖：
 修复1 A/B：
  1) target_from_entry / entry_from_target 往返后 poster/date/library_name 不丢
  2) entry_from_target 缺 date 时补当前时间、library_name 空时回退
  3) enqueue_auto_transfer_targets 用现有缓存回填 poster/date（空字段才回填，不覆盖已有值）
 修复2：
  4) auto_transfer_timeline_mode 三层同步（类属性/解析/非法值回退 degrade）
  5) off 模式 fix_timeline=False（不调轴，直接写原字幕）
  6) degrade 模式低可信 → 不 raise，写入未调轴原字幕，timeline_result 标记未应用
  7) strict 模式低可信 → 抛 409，不写入
  8) force_low_confidence 仍强制写入调轴结果（上游行为保留）
  9) 回归：手动上传默认 strict（不传 timeline_mode）行为不变
"""
import asyncio
import os
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PARENT = os.path.abspath(os.path.join(HERE, "..", "plugins"))
sys.path.insert(0, PLUGIN_PARENT)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))


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
    metainfo.MetaInfoPath = type("MetaInfoPath", (), {"__init__": lambda self, *a, **k: None})
    core.metainfo = metainfo

    db = mod("app.db")
    models = mod("app.db.models")
    th = mod("app.db.models.transferhistory")
    th.TransferHistory = type("TransferHistory", (), {})
    models.transferhistory = th
    db.models = models
    app.db = db

    plugins = mod("app.plugins")
    plugins._PluginBase = type("_PluginBase", (), {})
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
    stypes.MediaType = type("MediaType", (), {"MOVIE": "movie", "TV": "tv"})
    stypes.EventType = type("EventType", (), {"TransferComplete": "transfer.complete"})
    schemas.types = stypes
    app.schemas = schemas

    chain = mod("app.chain")
    tmdb = mod("app.chain.tmdb")
    tmdb.TmdbChain = type("TmdbChain", (), {})
    chain.tmdb = tmdb
    app.chain = chain


install_stubs()

from fastapi import HTTPException  # noqa: E402
from subtitlemanualupload.catalog.media_target_resolver import MediaTargetResolver  # noqa: E402
from subtitlemanualupload.api.status_api import StatusApi  # noqa: E402
from subtitlemanualupload.matching.subtitle_writer import (  # noqa: E402
    SubtitleWriter,
    timeline_result_blocks_auto_write,
    timeline_rejection_message,
)
from subtitlemanualupload.config.config_schema import (  # noqa: E402
    normalize_auto_transfer_timeline_mode,
    normalize_plugin_config,
)
from subtitlemanualupload.auto_transfer.auto_transfer_write import (  # noqa: E402
    AutoTransferWriteStrategy,
    AutoTransferWriteCollaborators,
)

NORMALIZE = lambda v: str(v or "").strip()


def make_resolver():
    return MediaTargetResolver(
        settings_obj=types.SimpleNamespace(RMT_MEDIAEXT={".mkv", ".mp4", ".strm"}),
        meta_info_path=None,
        stream_exts={".strm"},
        trust_transfer_history_paths=False,
        normalize_text=NORMALIZE,
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


# ============================================================ 1) 往返对称
print("=" * 72)
print("1) target_from_entry / entry_from_target 往返后 poster/date 不丢")
resolver = make_resolver()
entry_full = {
    "id": "t1",
    "origin": "transfer_history",
    "media_key": "mk1",
    "target_label": "S01E01 · Show.S01E01.mkv",
    "basename": "Show.S01E01",
    "filename": "Show.S01E01.mkv",
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
    "poster_url": "https://image.tmdb.org/t/p/w500/abc.jpg",
    "poster_thumb_url": "https://image.tmdb.org/t/p/w185/abc.jpg",
    "date": "2026-10-09 19:09:40",
}
target = resolver.target_from_entry(entry_full, subtitles=[])
check("target 透传 poster_url", target.get("poster_url") == entry_full["poster_url"], str(target.get("poster_url")))
check("target 透传 poster_thumb_url", target.get("poster_thumb_url") == entry_full["poster_thumb_url"])
check("target 透传 date", target.get("date") == entry_full["date"])
back = resolver.entry_from_target(target)
check("往返 poster_url 不丢", back.get("poster_url") == entry_full["poster_url"], str(back.get("poster_url")))
check("往返 poster_thumb_url 不丢", back.get("poster_thumb_url") == entry_full["poster_thumb_url"])
check("往返 date 不丢", back.get("date") == entry_full["date"], str(back.get("date")))
check("往返 library_name 不丢", back.get("library_name") == "MoviePilot 媒体库")

print()
print("2) entry_from_target 默认值补齐")
t2 = {"id": "t2", "label": "x", "path": "/media/a.mkv", "media_type": "movie", "title": "A"}
e2 = resolver.entry_from_target(t2)
check("缺 date 补当前时间", bool(e2.get("date")) and len(e2["date"]) >= 19, str(e2.get("date")))
check("library_name 空回退默认", e2.get("library_name") == "MoviePilot 媒体库", str(e2.get("library_name")))

print()
print("3) enqueue_auto_transfer_targets 用现有缓存回填（空字段才回填）")


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class FakeCatalog:
    def __init__(self, entries):
        self._entries = entries
        self.merged = None

    def entries(self):
        return list(self._entries)

    def merge_local_entries_cache(self, entries):
        self.merged = entries


class FakeAutoTransfer:
    def enqueue_transfer_auto_entries(self, entries):
        return len(entries), 0

    def auto_transfer_queue_snapshot(self, limit=100):
        return {"summary": {"total": 1}, "tasks": []}


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


# 缓存里已有 非自然死亡（media_key 由同一公式算出）带 poster/date
# 前端 target 不带 poster/date（模拟真实提交）
submit_target = {
    "id": "t1", "label": entry_full["target_label"], "basename": "Show.S01E01",
    "path": "/media/Show/Show.S01E01.mkv", "media_type": "tv", "title": "Show",
    "tmdb_id": 12345, "season": 1, "episode": 1, "year": "2020",
    "library_name": "MoviePilot 媒体库", "storage": "local", "writable": True,
}
cached_entry = resolver.entry_from_target(submit_target)
cached_entry.update(
    poster_url="https://image.tmdb.org/t/p/w500/cached.jpg",
    poster_thumb_url="https://image.tmdb.org/t/p/w185/cached.jpg",
    date="2026-10-08 17:37:59",
)
catalog = FakeCatalog([cached_entry])
owner = FakeOwner(FakeServices(resolver, catalog, FakeAutoTransfer()))
resp = asyncio.run(StatusApi(owner).enqueue_auto_transfer_targets(FakeRequest({"targets": [submit_target]})))
merged = catalog.merged[0]
check("回填 poster_url（取缓存值）", merged.get("poster_url") == cached_entry["poster_url"], str(merged.get("poster_url")))
check("回填 poster_thumb_url", merged.get("poster_thumb_url") == cached_entry["poster_thumb_url"])
check("回填 date（不排到最后）", merged.get("date") == cached_entry["date"], str(merged.get("date")))
check("端点返回 success", resp.get("success") is True)

# 不覆盖已有值：target 自带 poster 时保留 target 的
submit_with_poster = dict(submit_target, poster_url="https://image.tmdb.org/t/p/w500/target.jpg")
catalog2 = FakeCatalog([cached_entry])
owner2 = FakeOwner(FakeServices(resolver, catalog2, FakeAutoTransfer()))
asyncio.run(StatusApi(owner2).enqueue_auto_transfer_targets(FakeRequest({"targets": [submit_with_poster]})))
check("target 已有 poster 不被缓存覆盖", catalog2.merged[0]["poster_url"].endswith("target.jpg"),
      str(catalog2.merged[0]["poster_url"]))

# 缓存无该 media_key：date 用默认补齐，poster 仍为空（不崩）
catalog3 = FakeCatalog([])
owner3 = FakeOwner(FakeServices(resolver, catalog3, FakeAutoTransfer()))
asyncio.run(StatusApi(owner3).enqueue_auto_transfer_targets(FakeRequest({"targets": [submit_target]})))
check("无缓存时 date 仍补齐", bool(catalog3.merged[0].get("date")))

# ============================================================ 4) 配置三层
print()
print("4) auto_transfer_timeline_mode 配置解析")
check("默认 degrade", normalize_auto_transfer_timeline_mode(None) == "degrade")
check("off 合法", normalize_auto_transfer_timeline_mode("off") == "off")
check("strict 合法", normalize_auto_transfer_timeline_mode("strict") == "strict")
check("非法值回退 degrade", normalize_auto_transfer_timeline_mode("bogus") == "degrade")
check("大写容错", normalize_auto_transfer_timeline_mode("STRICT") == "strict")
norm = normalize_plugin_config({"auto_transfer_timeline_mode": "strict"})
check("normalize_plugin_config 解析 strict", norm.get("auto_transfer_timeline_mode") == "strict", str(norm.get("auto_transfer_timeline_mode")))
norm2 = normalize_plugin_config({})
check("normalize_plugin_config 缺省 degrade", norm2.get("auto_transfer_timeline_mode") == "degrade")


# ============================================================ 5-9) 调轴分派
@dataclass
class FakeTimelineResult:
    enabled: bool = True
    applied: bool = False
    reason: str = ""
    base: str = "audio"
    offset_seconds: float = 0.0
    scale_factor: float = 1.0
    score: float = 0.0
    confidence: str = "low"
    score_margin: float = 0.0
    active_ratio: float = 0.0
    risk_flags: Optional[List[str]] = field(default_factory=list)

    def to_dict(self):
        return {
            "enabled": self.enabled, "applied": self.applied, "reason": self.reason, "base": self.base,
            "offset_seconds": self.offset_seconds, "scale_factor": self.scale_factor, "score": self.score,
            "confidence": self.confidence, "score_margin": self.score_margin,
            "active_ratio": self.active_ratio, "risk_flags": list(self.risk_flags or []),
        }


class WriterOwner:
    def __init__(self, mode="degrade", timeline_result=None):
        self._auto_transfer_timeline_mode = mode
        self._timeline_result = timeline_result
        self._traditional_to_simplified = False
        self._subtitle_exts = {".ass", ".srt", ".ssa", ".sbv", ".sub", ".vtt", ".webvtt"}
        self._stream_exts = {".strm"}
        self._danmu_link_enabled = False
        self.timeline_tasks = {}
        self.timeline_fix_calls = 0
        self.removed_marks = []
        self.services = types.SimpleNamespace(
            subtitle_inventory=lambda: types.SimpleNamespace(invalidate_directory=lambda d: None),
        )

    def _normalize_text(self, v):
        return NORMALIZE(v)

    def _set_timeline_task(self, operation, *, status, message="", timeline_result=None):
        self.timeline_tasks[operation["target_entry"]["id"]] = {
            "status": status, "message": message,
            "timeline": timeline_result.to_dict() if timeline_result else None,
        }

    def _run_timeline_fix(self, *, video_path, subtitle_path, output_path, allow_risky_offset=False):
        self.timeline_fix_calls += 1
        Path(output_path).write_text("FIXED(shifted)", encoding="utf-8")
        return self._timeline_result

    def _timeline_result_blocks_auto_write(self, result):
        return timeline_result_blocks_auto_write(result)

    def _timeline_rejection_message(self, result):
        return timeline_rejection_message(result)

    def _target_from_entry(self, entry):
        return {"label": entry.get("target_label")}

    def _invalidate_match_history_cache(self):
        pass

    def _remove_ext_marks(self, path):
        self.removed_marks.append(str(path))


def build_operation(tmp):
    video = tmp / "Show.S01E01.mkv"
    video.write_bytes(b"video")
    source = tmp / "src.srt"
    source.write_text("ORIGINAL", encoding="utf-8")
    return {
        "upload_info": {"upload_id": "u1", "source_name": "src.srt", "ext": ".srt"},
        "target_entry": {"id": "tid1", "target_label": "S01E01", "path": str(video), "basename": "Show.S01E01"},
        "video_path": video,
        "source_path": source,
        "language_suffix": "chi",
        "destination_name": "Show.S01E01.chi.srt",
        "destination_path": tmp / "Show.S01E01.chi.srt",
    }


def make_writer(owner):
    return SubtitleWriter(
        owner,
        http_exception=HTTPException,
        logger=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                      error=lambda *a, **k: None),
        timeline_result_type=FakeTimelineResult,
        timeline_fix_func=lambda **k: None,
        convert_subtitle_file_to_simplified=lambda a, b: False,
        load_session=lambda sid: (Path("."), {}),
        timeline_cache_dir=lambda: Path("."),
    )


low_conf = FakeTimelineResult(confidence="low", risk_flags=["low_score"], score=-0.026, score_margin=0.039)

print()
print("5) off 模式：不调轴，直接写原字幕")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    owner = WriterOwner(mode="off", timeline_result=low_conf)
    writer = make_writer(owner)
    op = build_operation(tmp)
    ops = [op]
    written, fixed, simplified = writer.write_operations_to_disk(
        session_dir=tmp / "sess", operations=ops, fix_timeline=False,
        force_low_confidence=False, timeline_mode="off",
    )
    check("off: 未调用调轴", owner.timeline_fix_calls == 0, str(owner.timeline_fix_calls))
    check("off: 写出 1 个", len(written) == 1)
    check("off: 内容为原字幕", op["destination_path"].read_text(encoding="utf-8") == "ORIGINAL",
          op["destination_path"].read_text(encoding="utf-8"))

print()
print("6) degrade 模式：低可信不 raise，写入未调轴原字幕")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    owner = WriterOwner(mode="degrade", timeline_result=low_conf)
    writer = make_writer(owner)
    op = build_operation(tmp)
    ops = [op]
    try:
        written, fixed, simplified = writer.write_operations_to_disk(
            session_dir=tmp / "sess", operations=ops, fix_timeline=True,
            force_low_confidence=False, timeline_mode="degrade",
        )
        raised = False
    except HTTPException:
        raised = True
        written = []
    check("degrade: 未抛 409", not raised)
    check("degrade: 已调用调轴", owner.timeline_fix_calls == 1, str(owner.timeline_fix_calls))
    check("degrade: 写出 1 个", len(written) == 1)
    check("degrade: 内容回退为原字幕（非调轴）", op["destination_path"].read_text(encoding="utf-8") == "ORIGINAL",
          op["destination_path"].read_text(encoding="utf-8"))
    check("degrade: timeline 标记未应用", op["timeline_result"].applied is False)
    check("degrade: timeline enabled=True", op["timeline_result"].enabled is True)
    check("degrade: 任务状态 completed", owner.timeline_tasks["tid1"]["status"] == "completed",
          str(owner.timeline_tasks.get("tid1")))

print()
print("7) strict 模式：低可信抛 409，不写入")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    owner = WriterOwner(mode="strict", timeline_result=low_conf)
    writer = make_writer(owner)
    op = build_operation(tmp)
    ops = [op]
    status = None
    try:
        writer.write_operations_to_disk(
            session_dir=tmp / "sess", operations=ops, fix_timeline=True,
            force_low_confidence=False, timeline_mode="strict",
        )
    except HTTPException as exc:
        status = exc.status_code
    check("strict: 抛 409", status == 409, str(status))
    check("strict: 未写盘", not op["destination_path"].exists())
    check("strict: 任务标记 failed", owner.timeline_tasks["tid1"]["status"] == "failed")

print()
print("8) force_low_confidence 仍强制写入调轴结果")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    owner = WriterOwner(mode="degrade", timeline_result=low_conf)
    writer = make_writer(owner)
    op = build_operation(tmp)
    ops = [op]
    written, fixed, simplified = writer.write_operations_to_disk(
        session_dir=tmp / "sess", operations=ops, fix_timeline=True,
        force_low_confidence=True, timeline_mode="degrade",
    )
    check("force: 写出 1 个", len(written) == 1)
    check("force: 写入调轴结果", op["destination_path"].read_text(encoding="utf-8") == "FIXED(shifted)",
          op["destination_path"].read_text(encoding="utf-8"))
    check("force: 无 409", True)

print()
print("9) 回归：手动上传默认 strict（不传 timeline_mode）")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    owner = WriterOwner(mode="degrade", timeline_result=low_conf)
    writer = make_writer(owner)
    op = build_operation(tmp)
    ops = [op]
    status = None
    try:
        writer.write_operations_to_disk(
            session_dir=tmp / "sess", operations=ops, fix_timeline=True, force_low_confidence=False,
        )
    except HTTPException as exc:
        status = exc.status_code
    check("手动上传低可信仍 409（行为不变）", status == 409, str(status))

# 高可信调轴正常应用（回归）
high_conf = FakeTimelineResult(confidence="high", applied=True, risk_flags=[], score=0.5, score_margin=0.3)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    owner = WriterOwner(mode="degrade", timeline_result=high_conf)
    writer = make_writer(owner)
    op = build_operation(tmp)
    ops = [op]
    written, fixed, simplified = writer.write_operations_to_disk(
        session_dir=tmp / "sess", operations=ops, fix_timeline=True,
        force_low_confidence=False, timeline_mode="degrade",
    )
    check("高可信调轴仍正常写入", op["destination_path"].read_text(encoding="utf-8") == "FIXED(shifted)",
          op["destination_path"].read_text(encoding="utf-8"))
    check("高可信 applied 保留", op["timeline_result"].applied is True)

print()
print("10) auto_transfer_write 按 mode 决定 fix_timeline")
captured = {}


class CaptureOwner:
    def __init__(self, mode):
        self._auto_transfer_timeline_mode = mode
        self._normalize_text = NORMALIZE
        self._extract_episode_hint = lambda name: None
        self._subtitle_writer = lambda: types.SimpleNamespace(
            build_write_operations=lambda items, um, tem: [],
            build_destination_name=lambda t, i: "x",
        )

    def _target_from_entry(self, entry):
        return {}


def run_write_strategy(mode):
    captured.clear()
    owner = CaptureOwner(mode)

    def fake_write_ops(**kwargs):
        captured.update(kwargs)
        return [], 0, 0

    strategy = AutoTransferWriteStrategy(
        owner,
        collaborators=AutoTransferWriteCollaborators(
            target_from_entry=lambda e: {},
            detect_language_profile=lambda n, b: {"label": "", "suffix": "chi"},
            write_operations_to_disk=fake_write_ops,
            prepare_online_ai_subtitle_overrides=lambda **k: ({}, []),
            submit_autosub_for_entries=lambda *a, **k: {},
            select_subtitle_items=lambda pu, t: [{"upload_id": "u1", "target_id": "tid1",
                                                  "language_suffix": "chi", "source_name": "s.srt"}],
        ),
    )
    entry = {"id": "tid1", "path": "/x.mkv", "target_label": "x", "basename": "x"}
    strategy.write_prepared_uploads_for_entries(
        target_entries=[entry],
        prepared_uploads=[{"upload_id": "u1", "source_name": "s.srt", "stored_path": "/tmp/none.srt", "ext": ".srt"}],
        session_dir=Path(tempfile.mkdtemp()),
    )
    return captured


cap = run_write_strategy("off")
check("off → fix_timeline=False", cap.get("fix_timeline") is False, str(cap.get("fix_timeline")))
check("off → timeline_mode=off 透传", cap.get("timeline_mode") == "off", str(cap.get("timeline_mode")))
cap = run_write_strategy("degrade")
check("degrade → fix_timeline=True", cap.get("fix_timeline") is True, str(cap.get("fix_timeline")))
check("degrade → timeline_mode=degrade 透传", cap.get("timeline_mode") == "degrade")
cap = run_write_strategy("strict")
check("strict → fix_timeline=True", cap.get("fix_timeline") is True, str(cap.get("fix_timeline")))
check("strict → timeline_mode=strict 透传", cap.get("timeline_mode") == "strict")

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
