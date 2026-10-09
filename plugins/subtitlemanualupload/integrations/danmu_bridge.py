"""弹幕刮削联动桥。

照 ``autosub_bridge.AutoSubBridge`` 的成熟范式：通过 MoviePilot 的
``PluginManager.running_plugins`` 拿到弹幕刮削插件（Danmu）实例，再调用其公开
方法 ``generate_danmu(file_path)`` 触发刮削与合并。

设计约束（用户拍板）：
- 联动默认关闭，由 ``danmu_link_enabled`` 控制，关闭时对现有行为零影响；
- 触发默认异步（``danmu_link_async``），不阻塞字幕写入返回；
- 任何异常只记日志并吞掉，绝不能因为弹幕失败而中断字幕流程；
- 弹幕插件未安装/未启用时返回明确提示，不抛异常。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 弹幕插件产物标记：Danmu 生成 ``{视频}.danmu.ass`` 与 ``{视频}.withDanmu.ass``。
# 海拉鲁侧按此标记排除弹幕产物，避免把合并结果当成外挂字幕造成「弹幕套弹幕」。
DANMU_PRODUCT_MARKER = "danmu"


def is_danmu_product(name: Any) -> bool:
    """文件名是否属于弹幕刮削产物（.danmu.ass / .withDanmu.ass）。"""
    return DANMU_PRODUCT_MARKER in str(name or "").lower()


class DanmuBridge:
    def __init__(
        self,
        owner: Any,
        *,
        plugin_manager: Any,
        logger: Any,
    ) -> None:
        self._owner = owner
        self._plugin_manager = plugin_manager
        self._logger = logger

    # ------------------------------------------------------------------
    # 插件定位
    # ------------------------------------------------------------------
    def danmu_plugin(self) -> Tuple[Any, str]:
        """从 PluginManager 拿 Danmu 插件实例，返回 (plugin, 失败原因)。"""
        if self._plugin_manager is None:
            return None, "MoviePilot 插件管理器不可用"
        try:
            running_plugins = self._plugin_manager().running_plugins or {}
        except Exception as exc:
            self._logger.warning("[SubtitleManualUpload] 读取运行中插件失败: %s", exc)
            return None, "读取运行中插件失败"
        plugin = running_plugins.get("Danmu") or running_plugins.get("danmu")
        if not plugin:
            for candidate in running_plugins.values():
                if candidate.__class__.__name__ == "Danmu":
                    plugin = candidate
                    break
        if not plugin:
            return None, "请先安装并启用弹幕刮削插件"
        return plugin, ""

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def danmu_status(self) -> Dict[str, Any]:
        """联动状态：enabled / installed / available / version / message。"""
        owner = self._owner
        enabled = bool(getattr(owner, "_danmu_link_enabled", False))
        status: Dict[str, Any] = {
            "enabled": enabled,
            "installed": False,
            "available": False,
            "plugin_name": "弹幕刮削",
            "plugin_version": "",
            "overwrite": bool(getattr(owner, "_danmu_link_overwrite", True)),
            "async": bool(getattr(owner, "_danmu_link_async", True)),
            "message": "请先安装并启用弹幕刮削插件",
        }
        if not enabled:
            status["message"] = "弹幕联动已关闭"
            return status
        plugin, reason = self.danmu_plugin()
        if not plugin:
            status["message"] = reason
            return status
        status["installed"] = True
        status["plugin_name"] = getattr(plugin, "plugin_name", "弹幕刮削")
        status["plugin_version"] = getattr(plugin, "plugin_version", "")
        try:
            running = bool(plugin.get_state()) if hasattr(plugin, "get_state") else True
        except Exception as exc:
            self._logger.warning("[SubtitleManualUpload] 读取弹幕插件状态失败: %s", exc)
            running = False
        status["available"] = running
        status["message"] = "可触发弹幕刮削" if running else "弹幕刮削插件未启用"
        return status

    # ------------------------------------------------------------------
    # 触发
    # ------------------------------------------------------------------
    def trigger_for_videos(self, video_paths: List[str]) -> Dict[str, Any]:
        """对写入完成的视频触发弹幕刮削（异常吞掉，只记日志）。"""
        owner = self._owner
        if not getattr(owner, "_danmu_link_enabled", False):
            return {"triggered": 0, "results": [], "skipped": [], "reason": "弹幕联动已关闭"}
        plugin, reason = self.danmu_plugin()
        if not plugin:
            self._logger.info("[SubtitleManualUpload] 弹幕联动跳过：%s", reason)
            return {"triggered": 0, "results": [], "skipped": [], "reason": reason}
        if not hasattr(plugin, "generate_danmu"):
            self._logger.warning("[SubtitleManualUpload] 弹幕插件版本过旧，缺少 generate_danmu")
            return {
                "triggered": 0,
                "results": [],
                "skipped": [],
                "reason": "弹幕刮削插件版本过旧，请更新后再启用联动",
            }

        overwrite = bool(getattr(owner, "_danmu_link_overwrite", True))
        results: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for raw_video in video_paths or []:
            video = str(raw_video or "").strip()
            if not video:
                continue
            video_path = Path(video)
            if not video_path.exists():
                skipped.append({"video": video, "reason": "文件不存在"})
                continue
            if not self._claim_video(video):
                skipped.append({"video": video, "reason": "刚刚已触发，跳过重复请求"})
                continue
            if not overwrite and self._merged_output_exists(video_path):
                skipped.append({"video": video, "reason": "已存在弹幕合并字幕，按配置跳过"})
                continue
            try:
                result = plugin.generate_danmu(video)
            except Exception as exc:
                self._logger.warning("[SubtitleManualUpload] 弹幕联动失败 %s: %s", video, exc)
                skipped.append({"video": video, "reason": str(exc)})
                continue
            outcome = getattr(result, "outcome", "")
            message = getattr(result, "message", "")
            danmu_count = getattr(result, "danmu_count", 0)
            output_file = getattr(result, "output_file", None)
            results.append(
                {
                    "video": video,
                    "outcome": outcome,
                    "message": message,
                    "count": danmu_count,
                    "output": output_file,
                }
            )
            self._logger.info(
                "[SubtitleManualUpload] 弹幕联动完成 video=%s outcome=%s count=%s output=%s",
                Path(video).name,
                outcome,
                danmu_count,
                output_file,
            )
        return {"triggered": len(results), "results": results, "skipped": skipped}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _merged_output_exists(self, video_path: Path) -> bool:
        """目录内是否已存在该视频的 ``*.withDanmu.ass`` 合并产物。"""
        try:
            for item in video_path.parent.iterdir():
                name = item.name
                if name.endswith(".withDanmu.ass") and name.startswith(f"{video_path.stem}"):
                    return True
        except Exception:
            return False
        return False

    def _claim_video(self, video: str) -> bool:
        """同视频短时间内的重复触发去重（自动入库场景可能反复调用）。"""
        owner = self._owner
        ttl = float(getattr(owner, "_danmu_link_dedupe_seconds", 90) or 0)
        if ttl <= 0:
            return True
        lock = getattr(owner, "_danmu_link_lock", None)
        recent = getattr(owner, "_danmu_link_recent", None)
        if lock is None or recent is None:
            return True
        now = time.time()
        with lock:
            last = recent.get(video)
            if last and now - last < ttl:
                return False
            recent[video] = now
            if len(recent) > 500:
                stale = [key for key, value in recent.items() if now - value > ttl]
                for key in stale:
                    recent.pop(key, None)
        return True


__all__ = ["DanmuBridge", "is_danmu_product", "DANMU_PRODUCT_MARKER"]
