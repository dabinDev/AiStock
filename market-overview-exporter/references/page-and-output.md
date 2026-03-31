# 页面与输出约定

## 覆盖页面

- `jingjia.md`
  - 页面：`https://duanxianxia.com/web/jjlive`
  - 能力：复用现有脚本，导出实时竞价列和页面当前可见的最近历史竞价列
- `jjyd.md`
  - 页面：`https://duanxianxia.com/mob/jjyd`
  - 能力：复用现有脚本，导出竞价异动与竞价抢筹
- `global.md`
  - 页面：`https://duanxianxia.com/web/global`
  - 能力：抓取指数卡片快照
- `ztlive.md`
  - 页面：`https://duanxianxia.com/web/ztlive`
  - 能力：抓取热门题材按钮与涨停直播表
- `yidong.md`
  - 页面：`https://duanxianxia.com/web/yidong`
  - 能力：复用现有脚本，导出盘中异动快照
- `pool.md`
  - 页面：`https://duanxianxia.com/web/pool`
  - 能力：复用现有脚本，导出涨停股池各标签页
- `amount.md`
  - 页面：`https://duanxianxia.com/web/amount`
  - 能力：抓取今日量能、预测量能、昨日量能
- `fupan.md`
  - 页面：`https://duanxianxia.com/web/fupan`
  - 能力：抓取顶部指标、板块强度列表、涨停分组表
- `platerotat.md`
  - 页面：`https://duanxianxia.com/web/platerotat`
  - 能力：抓取默认近 20 日板块轮动表，以及当前激活的数据源/区间选项
- `jinji.md`
  - 页面：`https://duanxianxia.com/`
  - 能力：复用现有脚本，导出首页底部涨停股票池快照

## 输出结构

```text
<output-root>/
├─ duanxianxia_composite_summary.md
├─ YYYY-MM-DD/
│  ├─ dashboard.md
│  ├─ ai-analysis.md
│  ├─ jingjia.md
│  ├─ jjyd.md
│  ├─ global.md
│  ├─ ztlive.md
│  ├─ yidong.md
│  ├─ pool.md
│  ├─ amount.md
│  ├─ fupan.md
│  ├─ platerotat.md
│  └─ jinji.md
```

## AI 解析入口约定

- `dashboard.md` 作为手动点击入口
- 每个页面一行，包含原始数据文件链接、来源页面链接、AI 解析入口链接
- `ai-analysis.md` 作为提示词容器
- 当前实现是 Markdown 可点击入口，不依赖额外前端或按钮插件

## 限制

- `jjyd.md`、`yidong.md`、`pool.md`、`jinji.md` 仍是实时快照，不支持历史回放
- `jingjia.md` 的历史部分依赖 `jjlive` 当前可见列，通常覆盖最近 5 个交易日
- `platerotat.md` 当前固定抓取页面默认激活的近 20 日视图
- 本 skill 先统一数据链路与解析入口，不在仓库内引入独立 Web UI
