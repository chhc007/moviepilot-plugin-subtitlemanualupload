#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海拉鲁 × 弹幕联动 本地验证（stub 宿主依赖）。

覆盖：
 1) is_danmu_product 识别
 2) 三层配置默认值 + 解析（danmu_link_* 默认 False/True/True）
 3) DanmuBridge：插件定位（三种键）、状态、触发（ok/absent/failed/异常/未装/去重/覆盖跳过）
 4) 循环防护：SubtitleInventory 排除 .danmu.ass / .withDanmu.ass
 5) 挂载点：SubtitleWriter.write_operations_to_disk 末尾触发（同步 + 异步 + 关闭时不触发）
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

import subtitlemanualupload.integrations.danmu_bridge as dbridge  # noqa: E402
from subtitlemanualupload.integrations.danmu_bridge import DanmuBridge, is_danmu_product  # noqa: E402
from subtitlemanualupload.config.config_schema import (  # noqa: E402
    build_default_config,
    normalize_plugin_config,
)
from subtitlemanualupload.catalog.subtitle_inventory import SubtitleInventory  # noqa: E402
from subtitlemanualupload.matching.subtitle_writer import SubtitleWriter  # noqa: E402
from subtitlemanualupload.matching.subtitle_language import (  # noqa: E402
    detect_language_profile,
    normalize_language_suffix,
    is_chinese_language_suffix,
)

print("=" * 72)
print("1) is_danmu_product 识别弹幕产物")
check("video.danmu.ass", is_danmu_product("video.danmu.ass"))
check("video.withDanmu.ass", is_danmu_product("video.withDanmu.ass"))
check("Video.WithDanmu.ASS 大小写不敏感", is_danmu_product("Video.WithDanmu.ASS"))
check("普通字幕不误判", not is_danmu_product("video.chi.ass"))
check("含 danmu 关键词的中性名", is_danmu_product("Show.danmu-extra.srt"))

print()
print("2) 配置三层默认值与解析")
dflt = build_default_config()
check("默认 danmu_link_enabled=False", dflt["danmu_link_enabled"] is False, str(dflt["danmu_link_enabled"]))
check("默认 danmu_link_overwrite=True", dflt["danmu_link_overwrite"] is True)
check("默认 danmu_link_async=True", dflt["danmu_link_async"] is True)
norm_empty = normalize_plugin_config({})
check("空配置解析 enabled=False", norm_empty["danmu_link_enabled"] is False)
check("空配置解析 overwrite=True", norm_empty["danmu_link_overwrite"] is True)
check("空配置解析 async=True", norm_empty["danmu_link_async"] is True)
norm_on = normalize_plugin_config(
    {"danmu_link_enabled": True, "danmu_link_overwrite": False, "danmu_link_async": False}
)
check("开启后 enabled=True", norm_on["danmu_link_enabled"] is True)
check("overwrite=False 解析", norm_on["danmu_link_overwrite"] is False)
check("async=False 解析", norm_on["danmu_link_async"] is False)

print()
print("3) DanmuBridge 插件定位 / 状态 / 触发")


class FakeResult:
    def __init__(self, outcome, message="", output_file=None, danmu_count=0):
        self.outcome = outcome
        self.message = message
        self.output_file = output_file
        self.danmu_count = danmu_count


class FakeDanmu:
    plugin_name = "弹幕刮削SHIELD专用版"
    plugin_version = "1.12.0"

    def __init__(self):
        self.calls = []

    def get_state(self):
        return True

    def generate_danmu(self, file_path):
        self.calls.append(file_path)
        return FakeResult("ok", "完成", output_file=file_path + ".danmu.ass", danmu_count=321)


class FakeOwner:
    _danmu_link_enabled = True
    _danmu_link_overwrite = True
    _danmu_link_async = True
    _danmu_link_dedupe_seconds = 90
    _danmu_link_recent = {}
    _danmu_link_lock = threading.Lock()


class FakeLogger:
    def _p(self, *a, **k):
        pass
    info = warning = error = debug = _p


class FakePM:
    def __init__(self, running):
        self.running_plugins = running


owner = FakeOwner()
logger = FakeLogger()

# 键名 Danmu
fake = FakeDanmu()
bridge = DanmuBridge(owner, plugin_manager=lambda: FakePM({"Danmu": fake}), logger=logger)
found, reason = bridge.danmu_plugin()
check("按 'Danmu' 键定位", found is fake and not reason, reason)
st = bridge.danmu_status()
check("状态 installed/available", st["installed"] and st["available"], str(st))
check("状态含版本", st["plugin_version"] == "1.12.0")

# 键名 danmu
b2 = DanmuBridge(owner, plugin_manager=lambda: FakePM({"danmu": fake}), logger=logger)
check("按 'danmu' 键定位", b2.danmu_plugin()[0] is fake)

# 兜底按类名
class Danmu(FakeDanmu):
    pass


b3 = DanmuBridge(owner, plugin_manager=lambda: FakePM({"whatever": Danmu()}), logger=logger)
check("兜底按类名 Danmu 匹配", b3.danmu_plugin()[0] is not None)

# 未安装
b4 = DanmuBridge(owner, plugin_manager=lambda: FakePM({}), logger=logger)
check("未安装返回明确原因", b4.danmu_plugin()[0] is None and "弹幕" in b4.danmu_plugin()[1])
st4 = b4.danmu_status()
check("未安装状态 message 提示", "安装" in st4["message"], st4["message"])

# 触发（真实文件）
tmp = Path(tempfile.mkdtemp(prefix="danmu_verify_"))
video = tmp / "Show.S01E01.mkv"
video.write_bytes(b"fake-video")
sub = tmp / "Show.S01E01.chi.ass"
sub.write_text("[Script Info]\n")
trig = bridge.trigger_for_videos([str(video)])
check("触发成功计数=1", trig["triggered"] == 1, str(trig))
check("调用 generate_danmu", fake.calls == [str(video)])
check("结果含 outcome/count", trig["results"][0]["outcome"] == "ok" and trig["results"][0]["count"] == 321)

# 去重：立刻再次触发应跳过
trig_dup = bridge.trigger_for_videos([str(video)])
check("短时间重复触发被去重", trig_dup["triggered"] == 0 and trig_dup["skipped"], str(trig_dup))

# 不存在的文件
trig_missing = bridge.trigger_for_videos([str(tmp / "nope.mkv")])
check("不存在文件跳过不报错", trig_missing["triggered"] == 0 and "不存在" in trig_missing["skipped"][0]["reason"])

# 异常吞掉
class BoomDanmu(FakeDanmu):
    def generate_danmu(self, file_path):
        raise RuntimeError("网络炸了")

owner2 = FakeOwner()
owner2._danmu_link_recent = {}
owner2._danmu_link_lock = threading.Lock()
b_boom = DanmuBridge(owner2, plugin_manager=lambda: FakePM({"Danmu": BoomDanmu()}), logger=logger)
video2 = tmp / "Show.S01E02.mkv"
video2.write_bytes(b"x")
trig_boom = b_boom.trigger_for_videos([str(video2)])
check("刮削异常被吞掉并记录 skipped", trig_boom["triggered"] == 0 and "网络炸了" in trig_boom["skipped"][0]["reason"], str(trig_boom))

# 关闭时直接返回
owner_off = FakeOwner()
owner_off._danmu_link_enabled = False
b_off = DanmuBridge(owner_off, plugin_manager=lambda: FakePM({"Danmu": fake}), logger=logger)
trig_off = b_off.trigger_for_videos([str(video)])
check("关闭时 triggered=0", trig_off["triggered"] == 0 and "关闭" in trig_off.get("reason", ""), str(trig_off))
check("关闭时状态 message=已关闭", b_off.danmu_status()["message"] == "弹幕联动已关闭")

# overwrite=False 跳过已有 .withDanmu.ass
owner_noov = FakeOwner()
owner_noov._danmu_link_overwrite = False
owner_noov._danmu_link_recent = {}
owner_noov._danmu_link_lock = threading.Lock()
tmp3 = Path(tempfile.mkdtemp(prefix="danmu_verify3_"))
v3 = tmp3 / "Show.S01E03.mkv"
v3.write_bytes(b"x")
(tmp3 / "Show.S01E03.withDanmu.ass").write_text("merged")
fake3 = FakeDanmu()
b_noov = DanmuBridge(owner_noov, plugin_manager=lambda: FakePM({"Danmu": fake3}), logger=logger)
trig_noov = b_noov.trigger_for_videos([str(v3)])
check("overwrite=False 跳过已有合并字幕", trig_noov["triggered"] == 0 and fake3.calls == [], str(trig_noov))

print()
print("4) 循环防护：字幕清单排除弹幕产物")
tmp4 = Path(tempfile.mkdtemp(prefix="danmu_verify4_"))
v4 = tmp4 / "Show.S01E04.mkv"
v4.write_bytes(b"video")
(tmp4 / "Show.S01E04.chi.ass").write_text("[Script Info]\n")
(tmp4 / "Show.S01E04.danmu.ass").write_text("[Script Info]\n")
(tmp4 / "Show.S01E04.withDanmu.ass").write_text("[Script Info]\n")

inv = SubtitleInventory(
    subtitle_exts={".ass", ".srt", ".ssa", ".sbv", ".sub", ".vtt", ".webvtt"},
    stream_exts={".strm"},
    embedded_text_codecs={"subrip"},
    embedded_image_codecs={"hdmv_pgs_subtitle"},
    embedded_probe_cache={},
    embedded_probe_cache_max_size=10,
    trust_transfer_history_paths=True,
    normalize_text=lambda v: str(v or "").strip(),
    normalize_language_suffix=normalize_language_suffix,
    detect_language_profile=lambda name, raw: detect_language_profile(name, raw, {".ass", ".srt"}),
    is_chinese_language_suffix=is_chinese_language_suffix,
    safe_int=lambda v, d=0: d,
    subtitle_backup_path=lambda p: p.with_name(p.name + ".mp-timeline-bk"),
    subprocess_module=None,
    logger_warning=lambda *a, **k: None,
)
target = {"id": "t1", "path": str(v4), "storage": "local"}
files = inv.subtitle_files_for_target(target)
names = sorted(item["name"] for item in files)
check("清单仅含原生字幕", names == ["Show.S01E04.chi.ass"], str(names))
check("排除 .danmu.ass", "Show.S01E04.danmu.ass" not in names)
check("排除 .withDanmu.ass", "Show.S01E04.withDanmu.ass" not in names)

# remove_ext_marks 不重命名弹幕产物
(tmp4 / "Show.S01E04.danmu.default.ass").write_text("x")
inv.remove_ext_marks(v4)
check("remove_ext_marks 不动弹幕产物", (tmp4 / "Show.S01E04.danmu.default.ass").exists())

print()
print("5) 挂载点：write_operations_to_disk 末尾触发")


class _FakeInventory:
    def invalidate_directory(self, directory):
        pass


class WriterOwner(FakeOwner):
    def __init__(self, enabled, is_async, bridge_obj):
        self._danmu_link_enabled = enabled
        self._danmu_link_async = is_async
        self._danmu_link_overwrite = True
        self._danmu_link_recent = {}
        self._danmu_link_lock = threading.Lock()
        self._traditional_to_simplified = False
        self._bridge = bridge_obj
        self._target = {"id": "t1", "path": "", "basename": "Show.S01E05", "storage": "local"}
        self.services = types.SimpleNamespace(
            danmu_bridge=lambda: self._bridge,
            subtitle_inventory=lambda: _FakeInventory(),
        )

    def _remove_ext_marks(self, video_path):
        pass

    def _invalidate_match_history_cache(self):
        pass

    def _target_from_entry(self, entry):
        return {"label": entry.get("basename", "")}


def run_writer(owner_obj, video, source):
    writer = SubtitleWriter(
        owner_obj,
        http_exception=lambda **k: RuntimeError(k),
        logger=FakeLogger(),
        timeline_result_type=lambda **k: None,
        timeline_fix_func=lambda **k: None,
        convert_subtitle_file_to_simplified=lambda a, b: True,
        load_session=lambda sid: (None, {}),
        timeline_cache_dir=lambda: tmp,
    )
    dest_name = f"{video.stem}.chi.ass"
    ops = [
        {
            "upload_info": {"source_name": source.name, "archive_name": "", "upload_id": "u1"},
            "target_entry": owner_obj._target,
            "video_path": video,
            "source_path": source,
            "write_source_path": source,
            "language_suffix": "chi",
            "destination_name": dest_name,
            "destination_path": video.parent / dest_name,
        }
    ]
    return writer.write_operations_to_disk(session_dir=tmp, operations=ops)


# 5a 同步触发
tmp5 = Path(tempfile.mkdtemp(prefix="danmu_verify5_"))
v5 = tmp5 / "Show.S01E05.mkv"
v5.write_bytes(b"video")
src5 = tmp5 / "src.ass"
src5.write_text("[Script Info]\n")
fake5 = FakeDanmu()
o5 = WriterOwner(True, False, None)
o5._bridge = DanmuBridge(o5, plugin_manager=lambda: FakePM({"Danmu": fake5}), logger=logger)
written, fixed, simplified = run_writer(o5, v5, src5)
check("字幕已落盘", (tmp5 / "Show.S01E05.chi.ass").exists())
check("同步触发调用 generate_danmu", fake5.calls == [str(v5)], str(fake5.calls))
check("返回值结构不变", len(written) == 1 and fixed == 0 and simplified == 0)

# 5b 异步触发
tmp6 = Path(tempfile.mkdtemp(prefix="danmu_verify6_"))
v6 = tmp6 / "Show.S01E06.mkv"
v6.write_bytes(b"video")
src6 = tmp6 / "src.ass"
src6.write_text("[Script Info]\n")
fake6 = FakeDanmu()
o6 = WriterOwner(True, True, None)
o6._bridge = DanmuBridge(o6, plugin_manager=lambda: FakePM({"Danmu": fake6}), logger=logger)
run_writer(o6, v6, src6)
for _ in range(50):
    if fake6.calls:
        break
    time.sleep(0.02)
check("异步触发后台调用 generate_danmu", fake6.calls == [str(v6)], str(fake6.calls))

# 5c 关闭时不触发
tmp7 = Path(tempfile.mkdtemp(prefix="danmu_verify7_"))
v7 = tmp7 / "Show.S01E07.mkv"
v7.write_bytes(b"video")
src7 = tmp7 / "src.ass"
src7.write_text("[Script Info]\n")
fake7 = FakeDanmu()
o7 = WriterOwner(False, False, None)
o7._bridge = DanmuBridge(o7, plugin_manager=lambda: FakePM({"Danmu": fake7}), logger=logger)
run_writer(o7, v7, src7)
check("关闭时字幕仍正常落盘", (tmp7 / "Show.S01E07.chi.ass").exists())
check("关闭时不触发弹幕", fake7.calls == [], str(fake7.calls))

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
