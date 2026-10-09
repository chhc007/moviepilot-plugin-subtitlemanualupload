# 海拉鲁字幕大师SHIELD专用版

私有市场分发仓库，插件 ID `SubtitleManualUpload`（海拉鲁字幕大师SHIELD专用版）。

- **上游官方**：https://github.com/ifsherlock/MoviePilot-Plugins （`plugins.v2/subtitlemanualupload`）
- **本仓库结构**：根 `package.json` + `plugins/subtitlemanualupload/`（MoviePilot 私有市场分发规范）

## 维护方式（合并官方更新）

官方更新时按以下流程合并代码到本仓库：

```bash
# 1. 拉取官方最新
git fetch upstream main

# 2. 对比官方目录与本仓库差异（官方在 plugins.v2/，本仓在 plugins/）
git diff upstream/main -- plugins.v2/subtitlemanualupload

# 3. 将官方目录差异应用到本仓（用 --no-commit 方便检查）
git checkout upstream/main -- plugins.v2/subtitlemanualupload
#   （官方目录在 plugins.v2/ 下，需拷贝到 plugins/ 再整理）

# 4. 手动解决定制冲突（保留 chhc007 定制改动）

# 5. 同步根 package.json 版本与 history，提交
```

> 注意：官方目录结构为 `plugins.v2/<name>`，本仓库分发结构为 `plugins/<name>`，合并时注意路径映射。

## 定制记录

- v0.1.91 — 初始导入官方版（无定制），建立私有市场分发仓库
- v0.1.92~v0.1.94 — SHIELD 专用版定制（搜索修复、改名、迅雷影音源、弹幕刮削联动），详见 `package.json` history
- v0.1.95 — 新增「批量匹配字幕」：本地资源页勾选剧集后一键重新提交到自动入库队列（`auto_transfer`，走在线搜索→下载→写盘）；后端新增 `POST /auto_transfer_queue/enqueue` 与 `entry_from_target()`（target→entry 字段映射），前端桌面端工具栏 + 移动端「更多批量操作」各加按钮，带二次确认。改动文件：`catalog/media_target_resolver.py`、`api/status_api.py`、`api/routes.py`、`src/api/subtitleManualUploadApi.js`、`src/composables/useAutoTransferQueue.js`、`src/components/TargetDetailPanel.vue`、`src/components/AppPage.vue`、`src/mobile/MobileSubtitleDetail.vue`、`dist/`。