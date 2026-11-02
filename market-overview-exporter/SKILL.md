---
name: market-overview-exporter
description: 统一抓取竞价封单、竞价异动/抢筹、指数行情、涨停直播、异动播报、涨停股池、成交额、日期复盘、板块轮动和近期涨停快照，输出到 `data/YYYY-MM-DD/`，并生成 `dashboard.md` 与 `ai-analysis.md`。
---

# 市场总览导出
优先使用 [scripts/fetch_market_overview.py](E:/Github/Astock/market-overview-exporter/scripts/fetch_market_overview.py)，不要临时重写多页面抓取编排逻辑。

## 执行步骤
1. 确认输出根目录。
   默认使用 `data`
2. 根据需求选择抓取目标。
   默认 `--targets all`
3. 运行综合脚本。
4. 检查日期目录中的入口文件。
   `dashboard.md` 用于手动查看
   `ai-analysis.md` 用于 AI 分析入口
5. 如需确认页面结构和输出约定，再读 [references/page-and-output.md](E:/Github/Astock/market-overview-exporter/references/page-and-output.md)。

## 命令模板
```bash
python "E:/Github/Astock/market-overview-exporter/scripts/fetch_market_overview.py" --output-root "data"
python "E:/Github/Astock/market-overview-exporter/scripts/fetch_market_overview.py" --output-root "data" --targets "global,ztlive,amount"
python "E:/Github/Astock/market-overview-exporter/scripts/fetch_market_overview.py" --output-root "data" --targets "jingjia,jjyd"
python "E:/Github/Astock/market-overview-exporter/scripts/fetch_market_overview.py" --output-root "data" --targets "fupan,platerotat,pool,jinji"
python "E:/Github/Astock/market-overview-exporter/scripts/fetch_market_overview.py" --output-root "data" --date "2026-03-31" --targets "global,amount"
```

## 输出约定
- 日期目录固定为 `data/YYYY-MM-DD/`
- 原始页面数据文件名固定，便于 AI 检索
- 根目录汇总文件固定为 `data/market_overview_summary.md`

## 资源
- `scripts/fetch_market_overview.py`
- `references/page-and-output.md`
