#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务层集成验证：OnlineSubtitleSearchService 是否正确接入 thunder、摘除 zimuku。

用真实联网 fetcher，跑通 provider 注册 -> 搜索 -> 结果排序 全链路。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, "..", "plugins", "subtitlemanualupload"))
sys.path.insert(0, PLUGIN_ROOT)


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

# 用真实联网 fetcher 替换 OnlinePageClient，避免依赖 cloakbrowser/mp_browser 引擎
import ssl  # noqa: E402
import urllib.request  # noqa: E402

import online_subtitles.clients as clients  # noqa: E402


class _RealFetcher:
    def __init__(self, *a, **k):
        self.use_proxy = False

    def get_text(self, url, referer=""):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": clients.USER_AGENT, "Accept": "application/json,text/html,*/*",
        })
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace"), url

    def get_bytes(self, url, referer=""):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": clients.USER_AGENT})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            return url.rsplit("/", 1)[-1], r.read(), url

    def status(self):
        return {"engine": "api", "engine_name": "测试", "available": True,
                "cloakbrowser": False, "mp_browser": False, "proxy": False}

    def close(self):
        pass


clients.OnlinePageClient = _RealFetcher
import online_subtitles.shared as shared  # noqa: E402
shared.OnlinePageClient = _RealFetcher

from online_subtitles.service import OnlineSubtitleSearchService  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


print("=" * 72)
print("1) 服务实例化：provider 注册表")
svc = OnlineSubtitleSearchService(engine="cloakbrowser", use_proxy=False)
ids = sorted(svc.providers.keys())
print("   注册的 providers:", ids)
check("含 thunder", "thunder" in svc.providers)
check("不含 zimuku", "zimuku" not in svc.providers, str(ids))
check("保留 subhd/assrt/opensubtitles",
      all(k in svc.providers for k in ("subhd", "assrt", "opensubtitles")))
check("manual_providers 含 thunder", "thunder" in svc.manual_providers)
check("manual_providers 不含 zimuku", "zimuku" not in svc.manual_providers)

print()
print("2) status() 输出")
st = svc.status()
names = [p["id"] for p in st["providers"]]
print("   status providers:", names)
check("status 含 thunder", "thunder" in names)
check("status 不含 zimuku", "zimuku" not in names)

print()
print("3) 真实搜索（thunder）")
targets = [{"title": "泰坦尼克号", "year": "1997", "media_type": "movie",
            "basename": "Titanic.1997.1080p.mkv", "path": ""}]
res = svc.search(keywords=["泰坦尼克号"], providers=["thunder"], targets=targets, scope="auto")
results = res.get("results") or []
print(f"   返回 {len(results)} 条")
check("搜索返回结果", len(results) > 0)
if results:
    check("结果 provider 均为 thunder", all(r["provider"] == "thunder" for r in results))
    check("结果含 download_url", all(r.get("download_url") for r in results))
    print("   示例:", results[0]["title"][:50], "|", results[0].get("format"))

print()
print("4) 搜索（多源混合，验证排序不崩）")
res2 = svc.search(keywords=["泰坦尼克号"], providers=["thunder", "subhd"],
                  targets=targets, scope="auto")
r2 = res2.get("results") or []
provs = sorted({r["provider"] for r in r2})
print(f"   返回 {len(r2)} 条，来源: {provs}")
check("混合搜索不抛异常", True)
check("thunder 结果在列", "thunder" in provs)
if len(r2) > 1:
    check("thunder 排在最前（优先级最高）", r2[0]["provider"] == "thunder",
          f"首条={r2[0]['provider']}")

print()
print("5) 下载（真实）")
if results:
    try:
        dl = svc.download(results[:1])
        check("下载返回内容", len(dl) == 1 and len(dl[0]["content"]) > 100,
              f"{len(dl[0]['content'])} 字节" if dl else "")
        if dl:
            c = dl[0]["content"]
            try:
                c.decode("utf-8")
                check("下载内容为 UTF-8", True)
            except UnicodeDecodeError:
                check("下载内容为 UTF-8", False)
    except Exception as exc:
        check("下载返回内容", False, f"{type(exc).__name__}: {exc}")

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
