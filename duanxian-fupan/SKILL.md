---
name: duanxian-fupan
description: A股收盘复盘 skill，调用综合导出脚本或对应子脚本，更新 `data/YYYY-MM-DD/` 下的 `pool.md`、`jinji.md`、`shortline.md`，用于用户要求收盘后整理连板天梯、近期涨停和东方财富短线股池复盘材料时。
---

# 短线复盘

这是收盘复盘的正式入口 skill。优先调用 [update_astock_session_knowledge.py](E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py) 的盘后阶段，不要单独拼接多个命令。

## 默认调用

```bash
python "E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py" --phase "after-close"
```

## 常用命令

```bash
python "E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py" --phase "after-close"
python "E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py" --phase "full" --date "2026-03-31"
python "E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py" --phase "after-close" --cookie "<eastmoney-cookie>"
python "E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py" --phase "after-close" --start-date "2026-03-01" --end-date "2026-03-31"
```

## 输出

- `data/YYYY-MM-DD/pool.md`
- `data/YYYY-MM-DD/jinji.md`
- `data/YYYY-MM-DD/shortline.md`

## 说明

- `pool.md` 默认保留 `涨停` 和 `连板` 两个标签页
- `jinji.md` 来自首页底部 `涨停股票池`
- `shortline.md` 适合盘后补采历史日期
- 加上 `--start-date` 和 `--end-date` 后，会批量补采区间内的 `shortline.md`
- 如果区间结束日正好是今天，脚本仍会尽量补今天的 `pool.md` 和 `jinji.md`
- 如果用户要求串联全流程，直接调用 `E:/Github/Astock/duanxian-workflow/scripts/update_astock_session_knowledge.py`
- 如果用户只说“收盘复盘”“更新连板天梯/近期涨停/短线股池”，优先使用这个 skill
