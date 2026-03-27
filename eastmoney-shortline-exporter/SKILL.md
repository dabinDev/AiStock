---
name: eastmoney-shortline-exporter
description: 使用东方财富涨停板行情网页对应的六个接口（涨停股池、昨日涨停股池、强势股池、次新股池、炸板股池、跌停股池）按指定日期或日期区间抓取短线数据，生成 YYYY-MM-DD 日期目录下的 shortline.md，并重建根目录汇总文件。用于用户要求按东方财富网页接口批量导出短线股池数据、补采历史日期、修复中文表头、或者按日期目录输出并追加外层汇总日期索引时。
---

# 东方财富短线导出

优先使用 `scripts/fetch_eastmoney_shortline.py`，不要临时重写采集逻辑。

## 执行步骤

1. 确认输出根目录；默认使用用户当前工作目录。
2. 确认日期参数：
   - 单日：`--date`
   - 区间：`--start-date` + `--end-date`
3. 如网页接口依赖登录态或 Cookie，传入 `--cookie`。
4. 运行脚本生成日期目录和 Markdown。
5. 检查输出：
   - `<output-root>/YYYY-MM-DD/shortline.md`
   - `<output-root>/eastmoney_shortline_summary.md`
6. 如用户要求补某一天或重跑某个区间，直接重跑；脚本会覆盖当天文件并重建根目录汇总。

## 命令模板

```bash
python "C:/Users/dabin/.codex/skills/eastmoney-shortline-exporter/scripts/fetch_eastmoney_shortline.py" --date 2026-03-24 --output-root "."
python "C:/Users/dabin/.codex/skills/eastmoney-shortline-exporter/scripts/fetch_eastmoney_shortline.py" --start-date 2026-02-01 --end-date 2026-03-27 --output-root "."
python "C:/Users/dabin/.codex/skills/eastmoney-shortline-exporter/scripts/fetch_eastmoney_shortline.py" --date 20260324 --output-root "." --cookie "<cookie>"
```

## 输出约定

- 日期目录名固定为 `YYYY-MM-DD`
- 日文件默认名固定为 `shortline.md`
- 根目录汇总文件默认名固定为 `eastmoney_shortline_summary.md`
- 所有 Markdown 使用 UTF-8 with BOM 写入，避免 Windows 下中文表头乱码
- 每个股池都必须输出中文表头；空数据也保留表头并标记“空返回”
- 根目录汇总文件按日期从新到旧排序，并链接到对应日期文件

## 字段与表头

需要确认网页列名、接口名、排序字段或强势股“入选理由”映射时，读取 `references/headers-and-output.md`。

## 实现要求

- 使用 Playwright 的 `request.new_context()` 发请求；不要依赖浏览器页面抓表格。
- 仅使用东方财富网页对应接口，不切换到其他数据源。
- 不要把日期目录再嵌套进额外的 `data/` 目录。
- 不要修改输出根目录下无关文件。
- 如果用户只要求创建或更新 skill，不要顺带执行 git 提交、push 或其他高风险操作。
