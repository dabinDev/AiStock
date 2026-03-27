# AiStock

基于东方财富网页接口的短线数据采集仓库。

当前仓库包含：

- `data/`：按交易日归档的短线数据
- `eastmoney-shortline-exporter/`：用于抓取东方财富短线数据的本地 skill
- `data/eastmoney_shortline_summary.md`：汇总文件
- `data/INDEX.md`：数据目录索引
- `data/highest_limit_up_board.png`：涨停最高板折线图

## 目录说明

```text
.
├─ data/
│  ├─ YYYY-MM-DD/
│  │  └─ shortline.md
│  ├─ eastmoney_shortline_summary.md
│  ├─ INDEX.md
│  ├─ highest_limit_up_board.csv
│  ├─ highest_limit_up_board.md
│  └─ highest_limit_up_board.png
└─ eastmoney-shortline-exporter/
   ├─ SKILL.md
   ├─ agents/openai.yaml
   ├─ references/headers-and-output.md
   └─ scripts/fetch_eastmoney_shortline.py
```

## 本地手动更新

抓取单日数据：

```powershell
python "eastmoney-shortline-exporter/scripts/fetch_eastmoney_shortline.py" --date 2026-03-27 --output-root "data"
```

运行自动更新脚本（会抓取最新日期、重建索引、更新最高板图表）：

```powershell
python "scripts/update_data_latest.py"
```

指定日期运行：

```powershell
python "scripts/update_data_latest.py" --date 2026-03-27
```

## GitHub Actions 自动更新

仓库已配置 GitHub Actions：

- 工作流文件：`.github/workflows/update-eastmoney-shortline.yml`
- 触发方式：
  - 手动触发 `workflow_dispatch`
  - 定时触发 `schedule`

当前定时表达式为：

- `30 8 * * 1-5`

即：

- **UTC 时间每周一到周五 08:30**
- 对应 **北京时间每周一到周五 16:30**

这个时间点位于 A 股收盘后，用于自动抓取当天最新短线数据并提交到仓库。

## 自动更新内容

GitHub Actions 每次执行会：

1. 调用 `eastmoney-shortline-exporter` 抓取最新日期数据到 `data/`
2. 重建 `data/eastmoney_shortline_summary.md`
3. 重建 `data/INDEX.md`
4. 重算 `data/highest_limit_up_board.csv`
5. 重绘 `data/highest_limit_up_board.png`
6. 如果仓库内容发生变化，则自动提交并推送

## 说明

- 数据来源页面：<https://quote.eastmoney.com/ztb/detail>
- GitHub Actions 的定时任务按工作日执行，但**法定节假日如果仍命中工作日 cron，接口可能返回空数据**
- 当前实现优先保证流程稳定、文件编码正确（UTF-8 with BOM）以及可持续自动更新
