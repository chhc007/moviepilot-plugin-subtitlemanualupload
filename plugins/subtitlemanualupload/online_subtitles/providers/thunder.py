from __future__ import annotations

import hashlib
import os

from ..clients import *  # noqa: F401,F403
from ..keyword_builder import *  # noqa: F401,F403
from ..language import *  # noqa: F401,F403
from ..matcher import *  # noqa: F401,F403
from ..models import *  # noqa: F401,F403
from ..shared import *  # noqa: F401,F403
from .base import BaseSubtitleProvider


# 迅雷影音在线字幕接口：按片名/文件名返回 JSON 列表，url 为字幕直链。
# 无需登录与鉴权；客户端另用视频文件 CID（SHA1 采样哈希）做精确匹配。
THUNDER_API_URL = "https://api-shoulei-ssl.xunlei.com/oracle/subtitle"
THUNDER_DOWNLOAD_REFERER = "https://api-shoulei-ssl.xunlei.com/"

# CID 采样参数（与迅雷影音客户端一致）：文件 >= 60KB 时取三处各 20KB 拼接后 SHA1
CID_CHUNK = 0x5000
CID_TOTAL = 0xF000

THUNDER_FORMATS = {"srt", "ass", "ssa", "sub"}


def compute_thunder_cid(path: str) -> str:
    """计算视频文件的迅雷 CID（SHA1 采样哈希）。

    文件 >= 60KB：SHA1( 文件[0:20KB] + 文件[size/3 : size/3+20KB] + 文件[size-20KB:] )
    文件 <  60KB：SHA1( 整个文件 )
    返回大写十六进制字符串；文件不可读时返回空串（调用方回退为按名称搜索）。
    """
    try:
        if not path or "://" in str(path):
            return ""
        if not os.path.isfile(path):
            return ""
        size = os.path.getsize(path)
        if size <= 0:
            return ""
        digest = hashlib.sha1()
        with open(path, "rb") as handle:
            if size < CID_TOTAL:
                digest.update(handle.read(size))
            else:
                for offset in (0, size // 3, size - CID_CHUNK):
                    handle.seek(offset)
                    digest.update(handle.read(CID_CHUNK))
        return digest.hexdigest().upper()
    except OSError:
        return ""


def _thunder_format(value: Any) -> str:
    return str(value or "").strip().lower().lstrip(".")


def _convert_subtitle_encoding(content: bytes, filename: str = "") -> bytes:
    """字幕源常为 GBK/Big5；统一转成 UTF-8，避免播放器乱码。

    已是合法 UTF-8 则原样返回；二进制格式（.sub）不处理。
    """
    suffix = os.path.splitext(str(filename or ""))[1].lower().lstrip(".")
    if suffix not in {"srt", "ass", "ssa"}:
        return content
    try:
        content.decode("utf-8")
        return content
    except UnicodeDecodeError:
        pass
    text = _decode_bytes(content, None)
    return text.lstrip("\ufeff").encode("utf-8")


class ThunderProvider(BaseSubtitleProvider):
    provider_id = "thunder"
    display_name = "迅雷影音"
    default_root_url = DEFAULT_PROVIDER_ROOTS["thunder"]

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status["message"] = "使用迅雷影音公开接口搜索，无需 API Key；有本地文件时按 CID 精确匹配"
        return status

    def manual_url(self, keyword: str) -> str:
        # 迅雷字幕接口本身即搜索入口，手动链接指向带关键词的接口地址，便于直接查看原始结果
        return f"{self.root_url}?{urlencode({'name': keyword})}"

    def search(self, keyword: str, targets: List[Dict[str, Any]], scope: str) -> List[OnlineSubtitleResult]:
        items = self._api_search(keyword)
        return self._build_results(items, keyword, targets)

    def search_all(self, keywords: List[str], targets: List[Dict[str, Any]], scope: str) -> List[OnlineSubtitleResult]:
        """优先用本地视频 CID 精确定位；命中则直接返回，否则回退按关键词搜索。

        迅雷接口按 name 搜索，但客户端会用视频文件 CID 校验命中结果。这里复刻该流程：
        先算 CID，再在结果中把 CID 完全一致的条目排前；若关键词搜索为空再尝试用
        目标文件名（含发布组标记）作为补充关键词。
        """
        local_cid = self._local_cid(targets)
        results: List[OnlineSubtitleResult] = []
        seen_ids: set[str] = set()
        for keyword in keywords:
            found = self._build_results(self._api_search(keyword), keyword, targets, local_cid=local_cid)
            for item in found:
                if item.result_id in seen_ids:
                    continue
                seen_ids.add(item.result_id)
                results.append(item)
            if results:
                break
        # 关键词全部落空时，用目标文件名再试一次（迅雷接口对文件名匹配也很敏感）
        if not results:
            for target in targets or []:
                filename = str(target.get("basename") or target.get("filename") or "").strip()
                if not filename:
                    continue
                found = self._build_results(self._api_search(filename), filename, targets, local_cid=local_cid)
                if found:
                    results = found
                    break
        if local_cid:
            results.sort(key=lambda item: 0 if item.result_id.upper() == local_cid else 1)
        return results

    def _local_cid(self, targets: List[Dict[str, Any]]) -> str:
        for target in targets or []:
            path = str(target.get("path") or "").strip()
            if not path:
                continue
            cid = compute_thunder_cid(path)
            if cid:
                return cid
        return ""

    def _api_search(self, keyword: str) -> List[Dict[str, Any]]:
        query = str(keyword or "").strip()
        if not query:
            return []
        url = f"{self.root_url}?{urlencode({'name': query})}"
        status, text, final_url = self.fetcher.get_text(url, referer=THUNDER_DOWNLOAD_REFERER)
        if status >= 400 or not text:
            logger.warning(
                "[SubtitleManualUpload] 迅雷影音搜索接口不可用 keyword=%s status=%s",
                query,
                status,
            )
            return []
        try:
            payload = json.loads(text)
        except Exception:
            logger.warning(
                "[SubtitleManualUpload] 迅雷影音搜索返回非 JSON keyword=%s host=%s",
                query,
                _host(final_url),
            )
            return []
        if payload.get("code") != 0:
            return []
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _build_results(
        self,
        items: List[Dict[str, Any]],
        keyword: str,
        targets: List[Dict[str, Any]],
        *,
        local_cid: str = "",
    ) -> List[OnlineSubtitleResult]:
        results: List[OnlineSubtitleResult] = []
        for item in items:
            url = str(item.get("url") or "").strip()
            title = str(item.get("name") or item.get("simple_name") or "").strip()
            ext = _thunder_format(item.get("ext"))
            if not url or not title or ext not in THUNDER_FORMATS:
                continue
            cid = str(item.get("cid") or item.get("gcid") or "").strip().upper()
            cid_hit = bool(local_cid and cid and cid == local_cid)
            languages = [str(lang or "").strip() for lang in (item.get("languages") or []) if str(lang or "").strip()]
            language_text = " ".join([title, " ".join(languages), str(item.get("simple_name") or "")])
            language_label = _guess_language_label(language_text) or ("简体" if "默认" in languages else "")
            language_category = _language_category_from_text(language_text or language_label)
            season, episode = _episode_from_text(title) or (0, 0)
            assessment = _assess_result_match(title=title, keyword=keyword, targets=targets)
            if assessment["identity_status"] == "failed" and targets:
                # CID 完全命中时保留（同一文件对应的字幕，身份校验可能因命名差异误判）
                if not cid_hit:
                    continue
            results.append(
                OnlineSubtitleResult(
                    provider=self.provider_id,
                    provider_label=self.display_name,
                    # 迅雷接口返回的 url 里已含 cid 文件名，用它做稳定 result_id
                    result_id=cid or url.rsplit("/", 1)[-1],
                    title=title,
                    page_url=url,
                    download_url=url,
                    language=language_label,
                    language_category=language_category,
                    format=_guess_subtitle_format(" ".join([title, ext])),
                    season=season,
                    episode=episode,
                    score=assessment["score"] + (40 if cid_hit else 0),
                    source=self.display_name,
                    note="迅雷影音 CID 精确匹配" if cid_hit else "通过迅雷影音接口搜索",
                    downloadable=True,
                    relevance_status=assessment["relevance_status"],
                    region_bucket=_region_bucket({}, targets),
                    query_plan=_query_plan_for_keyword(keyword, targets)["label"],
                    identity_status=assessment["identity_status"],
                    reject_reason=assessment["reject_reason"],
                    match_detail=assessment["match_detail"],
                )
            )
        return _dedupe_results(results)

    def download(self, result: Dict[str, Any], captcha_code: str = "") -> Tuple[str, bytes]:
        download_url = str(result.get("download_url") or result.get("page_url") or "").strip()
        if not download_url:
            raise ValueError("迅雷影音缺少字幕下载地址")
        filename, content, final_url = self.fetcher.get_bytes(download_url, referer=THUNDER_DOWNLOAD_REFERER)
        if self._looks_like_html(content, filename):
            raise ValueError("迅雷影音返回了网页而不是字幕文件")
        content = _convert_subtitle_encoding(content, filename)
        logger.info(
            "[SubtitleManualUpload] 迅雷影音字幕下载完成 host=%s size=%s",
            _host(final_url),
            len(content),
        )
        return filename or f"thunder-{result.get('result_id') or 'subtitle'}.srt", content
