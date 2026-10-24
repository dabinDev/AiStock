// 真实浏览器验收专用：只监听回环地址，全部数据与配置都保存在内存，不访问上游。
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const labels = { jingjia: "竞价封单", jjyd: "竞价异动", global: "指数行情", ztlive: "涨停直播", yidong: "异动", pool: "涨停股池", amount: "成交额", fupan: "复盘", platerotat: "板块轮动", jinji: "连板晋级" };
const targets = Object.fromEntries(Object.entries(labels).map(([key, label]) => [key, { label, file: key + ".md" }]));
const today = "2026-10-21", calls = [], jobs = new Map(), records = [];
const progressPreview = process.argv.includes("--progress");
let model = "fixture-model-a", savedKey = true;
const settings = () => ({ kimiModel: model, hasKimiKey: savedKey, kimiKeyMask: "fixture…only" });
const metadata = date => Object.keys(targets).map(target => ({ target, label: labels[target], usable: true, exists: "true", quality: "ready", quality_message: "浏览器验收模拟资料", captured_at: date + " 09:25:00", data_date: date, row_count: 2, content: "# 浏览器验收模拟资料\n\n仅用于验证界面，不是真实行情。\n\n| 样例 | 数据 |\n| --- | --- |\n| 模拟 A | 100 |" }));
const prompt = "# 场景分析模板\n\n请结合所选资料给出可验证的结论。\n\n## 输入资料\n浏览器验收的模拟数据。";
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  let body = "";
  for await (const chunk of req) body += chunk;
  const payload = body ? JSON.parse(body) : null;
  calls.push({ method: req.method, route: url.pathname, model: payload?.model || payload?.kimi_model, keyProvided: Boolean(payload?.api_key || payload?.kimi_key), targets: payload?.targets, preset: payload?.preset });
  const reply = (value, status = 200) => { res.writeHead(status, {"Content-Type":"application/json; charset=utf-8"}); res.end(JSON.stringify(value)); };
  if (url.pathname === "/") {
    res.writeHead(200, {"Content-Type":"text/html; charset=utf-8"});
    return res.end(fs.readFileSync(path.join(root, "kanpan-tool/index.html"), "utf8").replaceAll("%TARGETS_JSON%", JSON.stringify(targets)).replaceAll("%DEFAULT_MODEL%", "fixture-model-a"));
  }
  if (url.pathname === "/assets/workspace.css") { res.writeHead(200, {"Content-Type":"text/css"}); return res.end(fs.readFileSync(path.join(root, "kanpan-tool/workspace.css"))); }
  if (url.pathname === "/api/state") { const date = url.searchParams.get("date") || today; return reply({ today, currentDate: date, dates: [today], targetData: metadata(date), availableTargets: Object.keys(targets), settings: settings(), history: records }); }
  if (url.pathname === "/api/settings") { if (payload) { model = payload.kimi_model; savedKey ||= Boolean(payload.kimi_key); } return reply({ok: true, ...settings()}); }
  if (url.pathname === "/api/models") return reply({ models: ["fixture-model-a", "fixture-model-b", "fixture-model-c"] });
  if (url.pathname === "/api/model-test") return payload.model === "unavailable-model" ? reply({error:"模拟：模型不存在或无权限"},404) : reply({ok:true,model:payload.model,reply:"OK（本地模拟）",elapsed_ms:15});
  if (url.pathname === "/api/prompt") return reply({ prompt, targets: payload.targets, preset: payload.preset });
  if (url.pathname === "/api/analyze" || url.pathname === "/api/fetch") {
    const id = "fixture-" + jobs.size, type = url.pathname.endsWith("analyze") ? "analyze" : "fetch";
    const record = {id: id + ".md", record_id:id + ".md", date:payload.date, targets:payload.targets, preset:payload.preset || "default", model:payload.model, prompt:payload.prompt_override || prompt, analysis:"# 本地模拟分析\n\n已验证从场景与资料选择到分析结果的完整界面流程。\n\n> 此内容用于界面验收，不是真实行情或模型输出。", context:{title_time:"10-21 09:25",generated_at:"2026-10-21 10:00:00"}};
    const running = progressPreview && type === "analyze";
    if (type === "analyze" && !running) records.unshift(record);
    jobs.set(id, { id, type, payload, started_at:Date.now(), status:running ? "running" : "completed", logs:["本地模拟任务完成"], target_statuses:Object.fromEntries(payload.targets.map(t=>[t,"completed"])), result:record });
    return reply({job_id:id});
  }
  if (url.pathname === "/api/job") {
    const job = jobs.get(url.searchParams.get("id"));
    if (job?.status === "running") {
      const elapsed = Date.now() - job.started_at;
      job.logs = [elapsed < 10000 ? "模型正在思考（本地模拟）" : "正在生成分析正文（本地模拟）"];
      if (elapsed >= 20000) { job.status = "completed"; job.logs.push("本地模拟任务完成"); records.unshift(job.result); }
    }
    return reply(job);
  }
  if (url.pathname === "/api/analysis") return reply(records.find(r=>r.id === url.searchParams.get("id")));
  if (url.pathname === "/api/history") return reply({records});
  if (url.pathname === "/fixture-evidence") return reply({calls, records, settings:settings()});
  return reply({error:"模拟服务未实现此接口"},404);
});
server.listen(0, "127.0.0.1", () => console.log("FIXTURE_URL=http://127.0.0.1:" + server.address().port + "/"));
