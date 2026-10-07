#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置逻辑验证：normalize_plugin_config / build_config_form 是否含 thunder、去 zimuku。"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PARENT = os.path.abspath(os.path.join(HERE, "..", "plugins"))
PLUGIN_ROOT = os.path.join(PLUGIN_PARENT, "subtitlemanualupload")
# 让 "subtitlemanualupload" 成为可导入的顶层包，从而支持其内部相对导入
sys.path.insert(0, PLUGIN_PARENT)


def _install_stubs():
    app = types.ModuleType("app")
    core = types.ModuleType("app.core")
    cfg = types.ModuleType("app.core.config")

    class _S:
        PROXY = None

    cfg.settings = _S()
    log = types.ModuleType("app.log")

    class _L:
        def _p(self, *a, **k):
            pass
        info = warning = error = debug = _p

    log.logger = _L()
    for n, m in [("app", app), ("app.core", core), ("app.core.config", cfg), ("app.log", log)]:
        sys.modules.setdefault(n, m)
    app.core = core
    core.config = cfg
    app.log = log


_install_stubs()

# config_schema 依赖包内相对导入，直接以包方式加载会触发 __init__.py（需要完整 MP 宿主）。
# 这里手工构造最小包结构，只加载需要的子模块。
import importlib.util  # noqa: E402

pkg = types.ModuleType("smu")
pkg.__path__ = [PLUGIN_ROOT]
sys.modules["smu"] = pkg
for sub in ("online", "online.online_subtitles", "matching", "matching.subtitle_language",
            "online_subtitles", "config"):
    m = types.ModuleType("smu." + sub)
    m.__path__ = [os.path.join(PLUGIN_ROOT, *sub.split("."))]
    sys.modules["smu." + sub] = m


def _load(mod_name, rel_path):
    spec = importlib.util.spec_from_file_location("smu." + mod_name, os.path.join(PLUGIN_ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smu." + mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# 先加载被依赖的底层模块，再加载 config_schema
_load("online_subtitles.clients", "online_subtitles/clients.py")
_load("online_subtitles.models", "online_subtitles/models.py")
_load("online_subtitles.language", "online_subtitles/language.py")
_load("online_subtitles.keyword_builder", "online_subtitles/keyword_builder.py")
_load("online_subtitles.matcher", "online_subtitles/matcher.py")
_load("online_subtitles.shared", "online_subtitles/shared.py")
_load("online_subtitles.providers", "online_subtitles/providers/__init__.py")
_load("online_subtitles.providers.base", "online_subtitles/providers/base.py")
_load("online_subtitles.providers.thunder", "online_subtitles/providers/thunder.py")
_load("online_subtitles.service", "online_subtitles/service.py")
# online.online_subtitle 与 matching.subtitle_language 是聚合再导出模块
import re as _re  # noqa: E402

_online_sub = _load("online.online_subtitle", "online/online_subtitle.py")
_lang = _load("matching.subtitle_language", "matching/subtitle_language.py")

# 把导出名挂到包上，供 config_schema 的相对导入解析
for _name in ("DEFAULT_ASSRT_API_URL", "DEFAULT_ENGINE", "DEFAULT_OPENSUBTITLES_API_URL",
              "DEFAULT_PROVIDER_ROOTS", "normalize_online_engine", "normalize_provider_roots"):
    setattr(sys.modules["smu.online.online_subtitle"], _name, getattr(_online_sub, _name, None))
for _name in ("DEFAULT_AUTO_FORMAT_PRIORITY", "DEFAULT_AUTO_LANGUAGE_PRIORITY",
              "normalize_auto_format_priority", "normalize_auto_language_priority"):
    setattr(sys.modules["smu.matching.subtitle_language"], _name, getattr(_lang, _name, None))

cfg_schema = _load("config.config_schema", "config/config_schema.py")

AVAILABLE_ONLINE_PROVIDER_IDS = cfg_schema.AVAILABLE_ONLINE_PROVIDER_IDS
DEFAULT_ONLINE_PROVIDER_IDS = cfg_schema.DEFAULT_ONLINE_PROVIDER_IDS
MANUAL_ONLINE_PROVIDER_IDS = cfg_schema.MANUAL_ONLINE_PROVIDER_IDS
normalize_plugin_config = cfg_schema.normalize_plugin_config
build_config_form = cfg_schema.build_config_form
normalize_online_site_urls = cfg_schema.normalize_online_site_urls

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


print("=" * 72)
print("1) 常量")
check("DEFAULT 含 thunder", "thunder" in DEFAULT_ONLINE_PROVIDER_IDS, str(DEFAULT_ONLINE_PROVIDER_IDS))
check("DEFAULT 不含 zimuku", "zimuku" not in DEFAULT_ONLINE_PROVIDER_IDS)
check("AVAILABLE 含 thunder", "thunder" in AVAILABLE_ONLINE_PROVIDER_IDS)
check("AVAILABLE 不含 zimuku", "zimuku" not in AVAILABLE_ONLINE_PROVIDER_IDS)
check("MANUAL 含 thunder", "thunder" in MANUAL_ONLINE_PROVIDER_IDS)
check("MANUAL 不含 zimuku", "zimuku" not in MANUAL_ONLINE_PROVIDER_IDS)

print()
print("2) 站点地址规范化")
urls = normalize_online_site_urls({})
check("含 thunder 键", "thunder" in urls, urls.get("thunder", ""))
check("thunder 指向迅雷接口", "xunlei.com" in urls.get("thunder", ""))
# zimuku 的 provider 代码与根地址刻意保留（normalize_provider_roots 遍历 DEFAULT_PROVIDER_ROOTS），
# 以便上游站点恢复时能一行重新接线；它不在任何可选源列表中，因此不会参与搜索。
check("zimuku 不在可选源列表（不会被使用）", "zimuku" not in AVAILABLE_ONLINE_PROVIDER_IDS)

print()
print("3) 旧配置兼容：用户配置里残留 zimuku")
legacy = {
    "online_providers": ["subhd", "zimuku"],
    "zimuku_url": "https://zimuku.la",
    "assrt_api_key": "k",
    "opensubtitles_api_key": "k",
}
cfg = normalize_plugin_config(legacy)
check("zimuku 被过滤掉", "zimuku" not in cfg["online_providers"], str(cfg["online_providers"]))
check("在线源列表非空", len(cfg["online_providers"]) > 0, str(cfg["online_providers"]))
check("thunder_url 已生成", "thunder" in cfg.get("online_site_urls", {}))
check("配置里无 zimuku_url", "zimuku_url" not in cfg)

print()
print("4) 显式选择 thunder")
cfg2 = normalize_plugin_config({"online_providers": ["subhd", "thunder"], "assrt_api_key": "k"})
check("thunder 保留", "thunder" in cfg2["online_providers"], str(cfg2["online_providers"]))

print()
print("5) 配置表单")
form, defaults = build_config_form()
text = repr(form)
check("表单含 thunder 选项", "'thunder'" in text)
check("表单不含 zimuku 选项", "'zimuku'" not in text)
check("表单含迅雷影音标签", "迅雷影音" in text)
check("默认配置含 thunder", "thunder" in defaults.get("online_providers", []), str(defaults.get("online_providers")))
check("默认配置含 thunder_url", "thunder_url" in defaults)
check("默认配置无 zimuku_url", "zimuku_url" not in defaults)

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
