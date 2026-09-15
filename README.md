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