#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海拉鲁 v0.1.97 修复本地验证（stub 宿主依赖）。

缺陷：视频已有中文字幕（内嵌/外挂）时跳过字幕下载，弹幕联动也不触发 → 该集没弹幕。
修复：字幕流程处理完毕（written 或 skipped）后都触发弹幕联动（方案 A）。

覆盖：
 1) search_and_write_entry 跳过分支（目标已有中文字幕）会触发 trigger_for_videos
 2) process_entry 跳过分支同样触发
 3) 开关关闭（_danmu_link_enabled=False）时不调用
 4) 异步模式（_danmu_link_async=True）后台线程调用
 5) 弹幕桥异常不影响 skipped 返回
 6) path 为空不调用
 7) 去重生效（90s TTL 内重复触发只刮一次）
 8) 回归：非跳过（无中文字幕）分支不触发弹幕（written 路径由 verify_danmu_link.py 覆盖）
"""
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

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
        RMT_MEDIAEXT = {".mkv", ".mp4"}

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

from subtitlemanualupload.integrations.danmu_bridge import DanmuBridge  # noqa: E402
from subtitlemanualupload.auto_transfer.auto_transfer_processor import (  # noqa: E402
    AutoTransferProcessor,
    AutoTransferProcessorCollaborators,
)


class FakeResult:
    def __init__(self, outcome="ok", message="完成", output_file=None, danmu_count=10):
        self.outcome = outcome
        self.message = message
        self.output_file = output_file
        self.danmu_count = danmu_count


class FakeDanmu:
    plugin_name = "弹幕刮削"
    plugin_version = "1.12.0"

    def __init__(self):
        self.calls = []

    def get_state(self):
        return True

    def generate_danmu(self, file_path):
        self.calls.append(file_path)
        return FakeResult(output_file=file_path + ".danmu.ass")


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def _p(self, *a, **k):
        pass

    info = debug = error = _p

    def warning(self, *a, **k):
        self.warnings.append(a)


class FakePM:
    def __init__(self, running):
        self.running_plugins = running


class FakeOwner:
    def __init__(self, *, enabled=True, is_async=False, bridge=None, path="/x.mkv", has_chinese=True):
        self._danmu_link_enabled = enabled
        self._danmu_link_async = is_async
        self._danmu_link_overwrite = True
        self._danmu_link_dedupe_seconds = 90
        self._danmu_link_recent = {}
        self._danmu_link_lock = threading.Lock()
        self._path = path
        self._has_chinese = has_chinese
        self.services = types.SimpleNamespace(danmu_bridge=lambda: bridge)

    def _normalize_text(self, v):
        return str(v or "").strip()


def make_processor(owner, logger, has_chinese_fn=None):
    def _has_chinese(entry, target):
        return owner._has_chinese if has_chinese_fn is None else has_chinese_fn(entry, target)

    collab = AutoTransferProcessorCollaborators(
        target_from_entry=lambda entry: {"label": entry.get("basename", "")},
        auto_target_has_chinese_subtitle=_has_chinese,
        chinese_transfer_media_evidence=lambda entry: (False, ""),
        media_for_transfer_entry=lambda entry: {},
        search_providers=lambda: [],
        search_keywords_for_entry=lambda entry, target: [],
        check_online_rate_limit=lambda providers: None,
        wait_online_rate_limit=lambda providers, task_ids=None: None,
        online_service=lambda: types.SimpleNamespace(),
        online_download_name=lambda name, content, result: name,
        extract_subtitle_files=lambda name, content, session_dir: [],
        write_prepared_uploads_for_entries=lambda **k: {},
        submit_autosub_for_entries=lambda *a, **k: {},
        call_search_write_subtitle=lambda *a, **k: {"status": "skipped", "reason": "stub"},
        submit_ai_for_entry=lambda entry, target, reason: {},
    )
    return AutoTransferProcessor(
        owner,
        logger=logger,
        http_exception=type("HttpExc", (Exception,), {}),
        collaborators=collab,
    )


def make_bridge(owner, fake_danmu, logger):
    return DanmuBridge(owner, plugin_manager=lambda: FakePM({"Danmu": fake_danmu}), logger=logger)


def wait_calls(fake, n=1, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(fake.calls) >= n:
            return True
        time.sleep(0.02)
    return len(fake.calls) >= n


# ---------------------------------------------------------------- 1) 跳过分支触发
print("=" * 72)
print("1) search_and_write_entry 跳过分支触发弹幕联动")
tmp = Path(tempfile.mkdtemp(prefix="v197_"))
video = tmp / "Show.S01E01.mkv"
video.write_bytes(b"fake-video")

logger = FakeLogger()
fake = FakeDanmu()
owner = FakeOwner(enabled=True, is_async=False, path=str(video), has_chinese=True)
owner.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner, fake, logger))
proc = make_processor(owner, logger)

result = proc.search_and_write_entry({"id": "t1", "path": str(video), "basename": "Show.S01E01"})
check("返回 skipped", result.get("status") == "skipped", str(result))
check("reason=目标已有中文字幕", result.get("reason") == "目标已有中文字幕", str(result.get("reason")))
check("跳过分支触发 generate_danmu", fake.calls == [str(video)], str(fake.calls))

# ---------------------------------------------------------------- 2) process_entry 跳过分支
print()
print("2) process_entry 跳过分支触发弹幕联动")
logger2 = FakeLogger()
fake2 = FakeDanmu()
owner2 = FakeOwner(enabled=True, is_async=False, path=str(video), has_chinese=True)
owner2._auto_skip_chinese_media_on_transfer = False
owner2._auto_transfer_subtitle_strategy = "auto"
owner2._normalize_auto_transfer_subtitle_strategy = lambda v: v or "auto"
owner2.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner2, fake2, logger2))
proc2 = make_processor(owner2, logger2)
res2 = proc2.process_entry({"id": "t2", "path": str(video), "basename": "Show.S01E01"})
check("process_entry 返回 skipped", res2.get("status") == "skipped", str(res2))
check("process_entry 跳过分支触发 generate_danmu", fake2.calls == [str(video)], str(fake2.calls))

# ---------------------------------------------------------------- 3) 开关关闭不调用
print()
print("3) 开关关闭时不调用")
logger3 = FakeLogger()
fake3 = FakeDanmu()
owner3 = FakeOwner(enabled=False, is_async=False, path=str(video), has_chinese=True)
owner3.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner3, fake3, logger3))
proc3 = make_processor(owner3, logger3)
res3 = proc3.search_and_write_entry({"id": "t3", "path": str(video), "basename": "Show.S01E01"})
check("关闭时仍返回 skipped", res3.get("status") == "skipped")
check("关闭时不触发 generate_danmu", fake3.calls == [], str(fake3.calls))

# ---------------------------------------------------------------- 4) 异步模式
print()
print("4) 异步模式后台线程触发")
logger4 = FakeLogger()
fake4 = FakeDanmu()
owner4 = FakeOwner(enabled=True, is_async=True, path=str(video), has_chinese=True)
owner4.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner4, fake4, logger4))
proc4 = make_processor(owner4, logger4)
res4 = proc4.search_and_write_entry({"id": "t4", "path": str(video), "basename": "Show.S01E01"})
check("异步仍返回 skipped", res4.get("status") == "skipped")
check("异步后台线程调用 generate_danmu", wait_calls(fake4, 1), str(fake4.calls))

# ---------------------------------------------------------------- 5) 异常不影响 skipped
print()
print("5) 弹幕桥异常不影响 skipped 返回")


class BoomBridge:
    def trigger_for_videos(self, paths):
        raise RuntimeError("弹幕炸了")


logger5 = FakeLogger()
owner5 = FakeOwner(enabled=True, is_async=False, path=str(video), has_chinese=True)
owner5.services = types.SimpleNamespace(danmu_bridge=lambda: BoomBridge())
proc5 = make_processor(owner5, logger5)
raised = False
try:
    res5 = proc5.search_and_write_entry({"id": "t5", "path": str(video), "basename": "Show.S01E01"})
except Exception:
    raised = True
    res5 = {}
check("异常未向外抛出", not raised)
check("异常时仍返回 skipped", res5.get("status") == "skipped", str(res5))
check("异常记了 warning", any("弹幕联动（跳过分支）" in str(w[0]) for w in logger5.warnings), str(logger5.warnings))

# 初始化（danmu_bridge()）异常也不影响
logger5b = FakeLogger()
owner5b = FakeOwner(enabled=True, is_async=False, path=str(video), has_chinese=True)


def _boom_factory():
    raise RuntimeError("桥初始化失败")


owner5b.services = types.SimpleNamespace(danmu_bridge=_boom_factory)
proc5b = make_processor(owner5b, logger5b)
raised_b = False
try:
    res5b = proc5b.search_and_write_entry({"id": "t5b", "path": str(video), "basename": "Show.S01E01"})
except Exception:
    raised_b = True
    res5b = {}
check("桥初始化异常未抛出", not raised_b)
check("桥初始化异常仍返回 skipped", res5b.get("status") == "skipped", str(res5b))

# ---------------------------------------------------------------- 6) path 为空不调用
print()
print("6) path 为空不调用")
logger6 = FakeLogger()
fake6 = FakeDanmu()
owner6 = FakeOwner(enabled=True, is_async=False, path="", has_chinese=True)
owner6.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner6, fake6, logger6))
proc6 = make_processor(owner6, logger6)
res6 = proc6.search_and_write_entry({"id": "t6", "path": "   ", "basename": "Show.S01E01"})
check("空 path 仍返回 skipped", res6.get("status") == "skipped")
check("空 path 不触发 generate_danmu", fake6.calls == [], str(fake6.calls))

# ---------------------------------------------------------------- 7) 去重
print()
print("7) 去重（90s TTL 内重复触发只刮一次）")
logger7 = FakeLogger()
fake7 = FakeDanmu()
owner7 = FakeOwner(enabled=True, is_async=False, path=str(video), has_chinese=True)
owner7.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner7, fake7, logger7))
proc7 = make_processor(owner7, logger7)
entry7 = {"id": "t7", "path": str(video), "basename": "Show.S01E01"}
proc7.search_and_write_entry(entry7)
proc7.search_and_write_entry(entry7)
check("重复触发只刮一次", fake7.calls == [str(video)], str(fake7.calls))

# ---------------------------------------------------------------- 8) 回归：非跳过分支不触发
print()
print("8) 回归：无中文字幕（非跳过）分支不触发弹幕联动")
logger8 = FakeLogger()
fake8 = FakeDanmu()
owner8 = FakeOwner(enabled=True, is_async=False, path=str(video), has_chinese=False)
owner8.services = types.SimpleNamespace(danmu_bridge=lambda: make_bridge(owner8, fake8, logger8))
proc8 = make_processor(owner8, logger8)
res8 = proc8.search_and_write_entry({"id": "t8", "path": str(video), "basename": "Show.S01E01"})
check("非跳过分支返回 skipped（无在线源）", res8.get("status") == "skipped", str(res8))
check("非跳过分支不触发 generate_danmu", fake8.calls == [], str(fake8.calls))

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
