#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迅雷 provider 本地验证：不依赖 MoviePilot 宿主，用 stub 模拟宿主模块。

覆盖：CID 算法、接口搜索、结果构造、编码转换、下载。
"""
import hashlib
import json
import os
import sys
import types

# ---- 1) 用 stub 顶掉 MoviePilot 宿主依赖，使 provider 可独立导入 ----
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..", "plugins", "subtitlemanualupload")
sys.path.insert(0, os.path.abspath(PLUGIN_ROOT))


def _install_stubs():
    # app.core.config.settings
    app = types.ModuleType("app")
    core = types.ModuleType("app.core")
    cfg = types.ModuleType("app.core.config")

    class _Settings:
        PROXY = None

    cfg.settings = _Settings()
    log = types.ModuleType("app.log")

    class _Logger:
        def _p(self, *a, **k):
            pass
        info = warning = error = debug = _p

    log.logger = _Logger()
    for name, mod in [("app", app), ("app.core", core), ("app.core.config", cfg), ("app.log", log)]:
        sys.modules.setdefault(name, mod)
    app.core = core
    core.config = cfg
    app.log = log


_install_stubs()

from online_subtitles.providers.thunder import (  # noqa: E402
    ThunderProvider,
    compute_thunder_cid,
    _convert_subtitle_encoding,
    THUNDER_API_URL,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


print("=" * 72)
print("1) CID 算法（与独立实现交叉验证）")
tmp = os.path.join(HERE, "tmp_cid_test.bin")
with open(tmp, "wb") as f:
    f.write(bytes((i * 7 + 13) % 256 for i in range(200000)))
size = os.path.getsize(tmp)
h = hashlib.sha1()
with open(tmp, "rb") as f:
    for off in (0, size // 3, size - 0x5000):
        f.seek(off)
        h.update(f.read(0x5000))
expect = h.hexdigest().upper()
got = compute_thunder_cid(tmp)
check("大文件 CID 与独立 SHA1 采样一致", got == expect, f"{got[:16]}...")

# 小文件分支
small = os.path.join(HERE, "tmp_cid_small.bin")
with open(small, "wb") as f:
    f.write(b"hello xunlei subtitle")
check("小文件 CID = 整文件 SHA1",
      compute_thunder_cid(small) == hashlib.sha1(b"hello xunlei subtitle").hexdigest().upper())
check("不存在的路径返回空串", compute_thunder_cid("/nope/nope.mkv") == "")
check("网络路径返回空串", compute_thunder_cid("http://x/y.mkv") == "")

print()
print("2) 编码转换")
gbk = "1\n00:00:01,000 --> 00:00:02,000\n你好世界\n".encode("gbk")
out = _convert_subtitle_encoding(gbk, "a.srt")
check("GBK 字幕转 UTF-8", out.decode("utf-8").strip().endswith("你好世界"))
utf8 = "你好".encode("utf-8")
check("已是 UTF-8 保持原样", _convert_subtitle_encoding(utf8, "a.srt") == utf8)
check("二进制 .sub 不转换", _convert_subtitle_encoding(b"\x00\x01\x02", "a.sub") == b"\x00\x01\x02")

print()
print("3) 真实接口搜索（联网）")
provider = ThunderProvider.__new__(ThunderProvider)
provider.root_url = THUNDER_API_URL


class _Fetcher:
    """最小 fetcher：真实联网 GET。"""
    use_proxy = False

    def get_text(self, url, referer=""):
        import ssl
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace"), url

    def get_bytes(self, url, referer=""):
        import ssl
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Chrome/125.0"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            name = url.rsplit("/", 1)[-1]
            return name, r.read(), url


provider.fetcher = _Fetcher()
items = provider._api_search("泰坦尼克号")
check("接口搜索返回结果", len(items) > 0, f"{len(items)} 条")
if items:
    check("结果含 url/name/ext", all(items[0].get(k) for k in ("url", "name", "ext")))

print()
print("4) 结果构造 + 身份校验")
targets = [{"title": "泰坦尼克号", "year": "1997", "media_type": "movie",
            "basename": "Titanic.1997.1080p.mkv"}]
results = provider._build_results(items[:20], "泰坦尼克号", targets)
check("构造出 OnlineSubtitleResult", len(results) > 0, f"{len(results)} 条")
if results:
    r0 = results[0]
    check("provider_id 正确", r0.provider == "thunder")
    check("downloadable=True", r0.downloadable is True)
    check("有 format", bool(r0.format), r0.format)

print()
print("5) CID 命中排序")
fake_cid = "ABC123" + "0" * 34
items_with_cid = [
    {"url": "https://x/a.srt", "name": "非命中.srt", "ext": "srt", "cid": "ZZZ", "languages": ["简体"]},
    {"url": "https://x/b.srt", "name": "命中.srt", "ext": "srt", "cid": fake_cid, "languages": ["简体"]},
]
res2 = provider._build_results(items_with_cid, "泰坦尼克号", targets, local_cid=fake_cid)
hit = [r for r in res2 if r.result_id == fake_cid]
check("CID 命中条目被构造", len(hit) == 1)
if hit:
    check("CID 命中得高分", hit[0].score >= 40, f"score={hit[0].score}")
    check("CID 命中标记 note", "CID" in hit[0].note)

print()
print("6) 下载 + 编码转换（真实下载一条）")
if results:
    cand = next((r for r in results if r.format in ("srt", "ass")), results[0])
    try:
        filename, content = provider.download(cand.to_dict())
        check("下载成功", len(content) > 100, f"{len(content)} 字节")
        check("内容是字幕文本（非 HTML）", b"<html" not in content[:300].lower())
        try:
            content.decode("utf-8")
            check("下载后为 UTF-8", True)
        except UnicodeDecodeError:
            check("下载后为 UTF-8", False, "仍是 GBK")
    except Exception as exc:
        check("下载成功", False, f"{type(exc).__name__}: {exc}")

for f in (tmp, small):
    try:
        os.remove(f)
    except OSError:
        pass

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
