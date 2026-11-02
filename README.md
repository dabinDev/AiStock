# AiStock

基于东方财富网页接口的短线数据采集仓库。

当前仓库包含：

- `data/`：按交易日归档的短线数据
- `eastmoney-shortline/`：用于抓取东方财富短线数据的内部实现目录
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
├─ eastmoney-shortline/
│  ├─ references/headers-and-output.md
│  └─ scripts/fetch_eastmoney_shortline.py
└─ scripts/
   └─ update_data_latest.py
```

## 本地手动更新

抓取单日数据：

```powershell
python "eastmoney-shortline/scripts/fetch_eastmoney_shortline.py" --date 2026-03-27 --output-root "data"
```

运行自动更新脚本：

```powershell
python "scripts/update_data_latest.py"
```

指定日期运行：

```powershell
python "scripts/update_data_latest.py" --date 2026-03-27
```

如果希望在本地也启用“非交易日自动跳过”，可以使用：

```powershell
python "scripts/update_data_latest.py" --skip-if-non-trading-day
```

## 本地看盘工具

启动综合看盘工具：
```powershell
python "scripts/run_kanpan_tool.py"
```

默认访问地址：
```text
http://127.0.0.1:8765
```

功能说明：
- 手动点击抓取 `竞价封单 / 竞价异动 / 指数行情 / 涨停实时直播 / 异动播报 / 涨停股池 / 成交额 / 日期复盘 / 板块轮动 / 近期涨停快照`
- 支持单页抓取、已选抓取、全量抓取
- 支持调用 Kimi API 做单页或全量复盘分析

Kimi 配置：
- 页面输入 Kimi API Key，或预先设置环境变量 `MOONSHOT_API_KEY`
- 默认模型为 `kimi-k2-thinking`
- 抓取结果和 Kimi 结果都落盘到 `data/YYYY-MM-DD/`

## GitHub Actions 自动更新

仓库已配置 GitHub Actions：

- 工作流文件：`.github/workflows/update-eastmoney-shortline.yml`
- 触发方式：
  - 手动触发 `workflow_dispatch`
  - 定时触发 `schedule`

当前定时表达式为：

- `30 8 * * 1-5`

即：

- **UTC 每周一到周五 08:30**
- 对应 **北京时间每周一到周五 16:30**

这个时间点位于 A 股收盘后，用于自动抓取当天最新短线数据并提交到仓库。

工作流会安装：

- `playwright`
- `matplotlib`
- `pandas_market_calendars`

其中 `pandas_market_calendars` 用于按 **上交所（SSE）交易日历** 判断当天是否为交易日。

### 手动触发 workflow

如果你想临时补跑一次，可以在 GitHub 网页上手动执行：

1. 打开仓库主页
2. 进入 **Actions**
3. 选择 **Update Eastmoney Shortline Data**
4. 点击 **Run workflow**
5. 可选填写 `target_date`
   - 例如：`2026-03-27`
   - 不填写则默认使用运行当天（北京时间）的日期
6. 点击确认运行

适用场景：

- 当天自动任务失败后手动补跑
- 想补抓某一个指定交易日
- 修改脚本后想立即验证效果

## 自动更新内容

GitHub Actions 每次执行会：

1. 先判断当天是否为 A 股交易日；如果不是，则直接跳过
2. 调用 `eastmoney-shortline` 抓取最新日期数据到 `data/`
3. 重建 `data/eastmoney_shortline_summary.md`
4. 重建 `data/INDEX.md`
5. 重算 `data/highest_limit_up_board.csv`
6. 重绘 `data/highest_limit_up_board.png`
7. 如果仓库内容发生变化，则自动提交并推送

## 说明

- 数据来源页面：<https://quote.eastmoney.com/ztb/detail>
- GitHub Actions 定时任务按工作日执行，同时会使用 **SSE 交易日历** 做二次判断，因此法定休市日会自动跳过
- 当前实现优先保证流程稳定、文件编码正确（UTF-8 with BOM）以及可持续自动更新
