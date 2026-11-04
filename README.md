# Solar cost drivers literature monitor

每周一自动运行：从 OpenAlex 抓取光伏成本驱动因素分解、学习曲线、BOS/软成本、LCOE/WACC、供应链与贸易/产业政策、投入产出/产业组织相关文献，并抓取 BNEF、Reuters、PV Magazine、NREL、LBNL 等信息源的新闻和报告。

## 上传
- 推荐新建一个 GitHub 仓库，上传本文件夹全部内容。
- Settings → Secrets 添加 `OPENAI_API_KEY`（必需）和 `OPENALEX_API_KEY`（推荐）。
- Actions → Run workflow，第一次 `backfill=true`。

## 改设置
- 只需改 `topics/solar_cost/config.json`：journals、queries、seed_dois、topic_groups、prefilter_rules、external.sources。
- 输出：`data/solar_cost/literature.xlsx`、`data/solar_cost/external_sources.xlsx`、`reports/solar_cost/latest.md`。

## 第一次运行后
- 日志 `WARNING: no OpenAlex source named exactly ... Closest: [...]` → 按 Closest 改期刊名。

## 本地 PDF
- `pdf_sync/` 三个文件放进同一个文件夹，双击 `START_PDF_SYNC_solar_cost.bat`。
- PDF 存到 `manu_12_TIM_APEN\literature_solar_cost`。
- 如果新仓库名不是 `seanli12354/nature-literature-monitor`，改 `download_github_pdfs.py` 里的 `REPO`。
