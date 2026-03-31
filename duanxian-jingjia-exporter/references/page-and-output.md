# 页面与输出约定

## 覆盖范围
- 竞价封单
- 竞价异动 / 竞价抢筹
- 指数行情
- 涨停实时直播
- 异动播报
- 涨停股池
- 成交额
- 日期复盘
- 板块轮动前 20 天强度
- 近期涨停快照

## 输出结构
```text
output-root/
├─ YYYY-MM-DD/
│  ├─ jingjia.md
│  ├─ jjyd.md
│  ├─ global.md
│  ├─ ztlive.md
│  ├─ yidong.md
│  ├─ pool.md
│  ├─ amount.md
│  ├─ fupan.md
│  ├─ platerotat.md
│  ├─ jinji.md
│  ├─ dashboard.md
│  └─ ai-analysis.md
└─ market_overview_summary.md
```

## 约定
- 页面导出文件仅保留数据结果，不写入来源站点说明。
- 汇总文件只保留日期、目标和入口，不暴露站点地址。
- AI 分析入口只保留分析框架和数据模块，不展示来源页提示。