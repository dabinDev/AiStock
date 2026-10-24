// 使用实际 HTML 和脚本；所有 API 都是本地内存模拟，不允许外部网络。
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const args = process.argv.slice(2);
const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : undefined;
const { JSDOM, VirtualConsole } = require(option("--jsdom") || "jsdom");
const root = path.resolve(__dirname, "..");
const targets = Object.fromEntries(["jingjia", "jjyd", "global", "ztlive", "yidong", "pool", "amount", "fupan", "platerotat", "jinji"].map(key => [key, { label: key, file: key + ".md" }]));
const html = fs.readFileSync(path.join(root, "kanpan-tool/index.html"), "utf8")
  .replaceAll("%TARGETS_JSON%", JSON.stringify(targets)).replaceAll("%DEFAULT_MODEL%", "model-a");
const clone = value => JSON.parse(JSON.stringify(value));
const reply = (data, status = 200) => ({ ok: status === 200, status, json: async () => clone(data) });
const pause = () => new Promise(resolve => setTimeout(resolve, 5));
async function until(check) {
  for (let i = 0; i < 400; i++) { if (check()) return; await pause(); }
  throw new Error("页面操作未在预期时间内完成");
}
function fixture(options = {}) {
  const app = { calls: [], model: "model-a", available: ["jingjia", "jjyd"], jobs: new Map(), failed: [], stale: [], pending: false, hook: null, quoteClock: { now:0, nextId:100000, timers:new Map() } };
  const context = { title_time: "10-20 10:05", generated_at: "2026-10-20 10:06:00", capture_start: "2026-10-20 10:04:00", capture_end: "2026-10-20 10:05:00" };
  const record = { id: "kimi-analysis-history.md", date: "2026-10-20", targets: ["jingjia", "jjyd", "yidong", "jinji"], preset: "intraday", model: "model-history", analysis: "历史结论", prompt: "历史提示词", context };
  const settings = () => ({ kimiModel: app.model, hasKimiKey: true, kimiKeyMask: "test****mask" });
  const metadata = () => Object.keys(targets).map(target => ({ target, label: target, exists: app.available.includes(target) ? "true" : "false", usable: app.available.includes(target) && !app.stale.includes(target) && !app.failed.includes(target), quality: app.failed.includes(target) ? "failed" : app.stale.includes(target) ? "stale" : app.available.includes(target) ? "ready" : "missing", quality_message: app.stale.includes(target) ? "动态数据超过 15 分钟，请更新后分析" : app.failed.includes(target) ? "本次抓取失败" : app.available.includes(target) ? "数据可用" : "尚未抓取", captured_at: "2026-10-20 10:05:00", data_date: "2026-10-20", row_count: 10, content: "原始证据" }));
  function job(type, payload) {
    const id = "fixture-" + app.jobs.size;
    if (type === "fetch") {
      app.available = [...new Set([...app.available, ...payload.targets.filter(t => !app.failed.includes(t))])];
      app.stale = app.stale.filter(t => !payload.targets.includes(t));
    }
    const failed = type === "fetch" && payload.targets.some(t => app.failed.includes(t));
    app.jobs.set(id, { id, type, status: app.pending ? "running" : failed ? "failed" : "completed", payload, logs: ["模拟执行"], target_statuses: Object.fromEntries(payload.targets.map(t => [t, failed && app.failed.includes(t) ? "failed" : "completed"])), error: failed ? "部分数据抓取失败" : "", result: { date: payload.date, targets: payload.targets, target_data: metadata().filter(x => payload.targets.includes(x.target)), preset: payload.preset || "default", prompt: "模拟提示词", analysis: type === "analyze" ? "模拟结论" : "", context, model: payload.model, record_id: record.id } });
    return reply({ job_id: id });
  }
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => errors.push(error.message));
  const dom = new JSDOM(html, { url: "https://workspace.test/", runScripts: "dangerously", pretendToBeVisual:true, virtualConsole,
    beforeParse(window) {
      if (options.quoteClock) {
        const originalSet = window.setTimeout.bind(window), originalClear = window.clearTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => {
          if (delay !== 10000) return originalSet(callback, delay, ...args);
          const id = ++app.quoteClock.nextId;
          app.quoteClock.timers.set(id, { at:app.quoteClock.now + delay, callback:()=>callback(...args) });
          return id;
        };
        window.clearTimeout = id => { if (!app.quoteClock.timers.delete(id)) originalClear(id); };
      }
      if (options.storedTargets) window.localStorage.setItem("astock.kanpan.selectedTargets.v1", JSON.stringify(options.storedTargets));
      if (options.storedScene) window.localStorage.setItem("astock.kanpan.scene.v1", options.storedScene);
      window.fetch = async (url, opts = {}) => {
        const parsed = new URL(String(url), "https://workspace.test/");
        assert.equal(parsed.origin, "https://workspace.test");
        const route = parsed.pathname, payload = opts.body ? JSON.parse(opts.body) : null;
        app.calls.push({ route, payload });
        if (app.hook) { const result = app.hook(route, payload, parsed); if (result) return await result; }
        if (route === "/api/state") return reply({ currentDate: parsed.searchParams.get("date") || "2026-10-20", today: "2026-10-20", dates: ["2026-10-20", "2026-10-19"], availableTargets: app.available, targetData: metadata(), history: [record], settings: settings() });
        if (route === "/api/settings") { if (payload) app.model = payload.kimi_model; return reply({ ok: true, ...settings() }); }
        if (route === "/api/models") return reply({ models: ["model-a", "model-b"] });
        if (route === "/api/model-test") return reply({ ok: true, model: payload.model, reply: "OK", elapsed_ms: 1 });
        if (route === "/api/fetch") return job("fetch", payload);
        if (route === "/api/analyze") return job("analyze", payload);
        if (route === "/api/job") return reply(app.jobs.get(parsed.searchParams.get("id")));
        if (route === "/api/prompt") return reply({ prompt: "模拟提示词", targets: payload.targets, preset: payload.preset, context });
        if (route === "/api/history") return reply({ records: [record] });
        if (route === "/api/analysis") return reply(record);
        throw new Error("未配置的测试 API：" + route);
      };
      window.XMLHttpRequest = class { constructor() { throw new Error("测试禁止 XHR"); } };
      window.WebSocket = class { constructor() { throw new Error("测试禁止 WebSocket"); } };
    }
  });
  return { app, dom, errors, node: id => dom.window.document.getElementById(id), metadata, record,
    advanceQuote(ms) {
      const end = app.quoteClock.now + ms;
      while (true) {
        const next = [...app.quoteClock.timers.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
        if (!next) break;
        app.quoteClock.now = next[1].at; app.quoteClock.timers.delete(next[0]); next[1].callback();
      }
      app.quoteClock.now = end;
    },
    ready: () => until(() => dom.window.document.getElementById("tradeDate").value === "2026-10-20"),
    scene(value) { const button = this.node("sceneSelect").querySelector(`[data-scene="${value}"]`); assert.ok(button, "需要明确的场景选择入口"); button.click(); },
    close: () => dom.window.close() };
}
const results = [];
async function test(name, run, options = {}) {
  if (option("--only") && !name.includes(option("--only"))) return;
  const f = fixture(options);
  let watchdog;
  try {
    await Promise.race([(async () => { await f.ready(); await run(f); })(),
      new Promise((_, reject) => { watchdog = setTimeout(() => reject(new Error("测试操作超时")), 8000); })]);
    assert.deepEqual(f.errors, []); results.push({ name, passed: true });
  }
  catch (error) { results.push({ name, passed: false, error: error.message, script_errors: f.errors, calls: f.app.calls }); }
  finally { clearTimeout(watchdog); f.close(); }
  console.log(name + ": " + (results.at(-1).passed ? "PASS" : results.at(-1).error));
}
(async () => {
  await test("轮换进度：提示语满十秒才切换且不会连续重复", async f => {
    const text = f.node("sidebarQuoteText").textContent;
    f.advanceQuote(9999); assert.equal(f.node("sidebarQuoteText").textContent, text);
    f.advanceQuote(1); assert.notEqual(f.node("sidebarQuoteText").textContent, text);
    const next = f.node("sidebarQuoteText").textContent;
    f.advanceQuote(10000); assert.notEqual(f.node("sidebarQuoteText").textContent, next);
    assert.equal(f.app.quoteClock.timers.size, 1);
  }, {quoteClock:true});
  await test("轮换进度：手动换一句后重新等待十秒", async f => {
    f.advanceQuote(9000); f.dom.window.document.querySelector(".quote-switch").click();
    const text = f.node("sidebarQuoteText").textContent;
    f.advanceQuote(9999); assert.equal(f.node("sidebarQuoteText").textContent, text);
    f.advanceQuote(1); assert.notEqual(f.node("sidebarQuoteText").textContent, text);
    assert.equal(f.app.quoteClock.timers.size, 1);
  }, {quoteClock:true});
  await test("轮换进度：页面隐藏停止轮换返回后恢复且离开时清理", async f => {
    const doc=f.dom.window.document, text=f.node("sidebarQuoteText").textContent;
    Object.defineProperty(doc,"hidden",{configurable:true,value:true});
    doc.dispatchEvent(new f.dom.window.Event("visibilitychange"));
    f.advanceQuote(30000); assert.equal(f.node("sidebarQuoteText").textContent,text);
    assert.equal(f.app.quoteClock.timers.size,0);
    Object.defineProperty(doc,"hidden",{configurable:true,value:false});
    doc.dispatchEvent(new f.dom.window.Event("visibilitychange"));
    f.advanceQuote(10000); assert.notEqual(f.node("sidebarQuoteText").textContent,text);
    f.dom.window.dispatchEvent(new f.dom.window.Event("pagehide"));
    assert.equal(f.app.quoteClock.timers.size,0);
    f.dom.window.dispatchEvent(new f.dom.window.Event("pageshow"));
    assert.equal(f.app.quoteClock.timers.size,1);
  }, {quoteClock:true});
  await test("轮换进度：分析期间显示页内进度和真实阶段并在完成后填满", async f => {
    f.app.pending=true;
    f.app.hook=(route)=>{if(route==="/api/job") [...f.app.jobs.values()].forEach(j=>{j.logs=["模型正在思考"];}); return null;};
    const work=f.dom.window.runPrimaryAction();
    await until(()=>f.app.calls.some(x=>x.route==="/api/job"));
    try {
      assert.ok(f.node("taskProgress"),"需要页内进度条");
      assert.equal(f.node("taskPanel").hidden,false);
      assert.ok(f.node("taskPanel").closest(".analysis-setup"));
      assert.equal(f.node("taskProgress").getAttribute("role"),"progressbar");
      assert.equal(f.node("taskProgress").hasAttribute("aria-valuenow"),false);
      assert.match(f.node("taskProgressTitle").textContent,/模型正在思考/);
      assert.equal(f.dom.window.document.querySelector('[aria-modal="true"], #loadingMask'),null);
    } finally {for(const job of f.app.jobs.values()) job.status="completed"; await work;}
    assert.equal(f.node("taskPanel").dataset.state,"completed");
    assert.equal(f.node("taskProgress").getAttribute("aria-valuenow"),"100");
    assert.match(f.node("taskProgressTitle").textContent,/分析完成/);
    f.node("tradeDate").value="2026-10-19"; await f.dom.window.refreshState();
    assert.equal(f.node("taskPanel").hidden,true);
  });
  await test("轮换进度：生成正文阶段可见失败后停止动画并保留错误", async f => {
    f.app.pending=true;
    f.app.hook=route=>{if(route==="/api/job") [...f.app.jobs.values()].forEach(j=>{j.logs=["模型正在思考","正在生成分析正文"];}); return null;};
    const work=f.dom.window.runPrimaryAction();
    await until(()=>f.app.calls.some(x=>x.route==="/api/job"));
    try {assert.match(f.node("taskProgressTitle")?.textContent||"",/正在生成分析正文/);}
    finally {for(const job of f.app.jobs.values()){job.status="failed";job.error="模拟额度不足";}await work;}
    assert.equal(f.node("taskPanel").dataset.state,"failed");
    assert.match(f.node("loadingText").textContent,/模拟额度不足/);
    assert.equal(f.node("taskProgress").hasAttribute("aria-valuenow"),false);
  });
  await test("轮换进度：查询中断停止进度可恢复且不重复提交", async f => {
    f.app.hook=route=>route==="/api/job"?reply({error:"模拟断线"},503):null;
    await f.dom.window.runPrimaryAction();
    assert.equal(f.node("taskPanel").dataset.state,"paused");
    assert.equal(f.node("taskPanel").hidden,false);
    f.app.hook=null; await f.dom.window.resumeActiveJob();
    assert.equal(f.node("taskPanel").dataset.state,"completed");
    assert.equal(f.app.calls.filter(x=>x.route==="/api/analyze").length,1);
  });
  await test("交互修复：已保存密钥不回填掩码且刷新不覆盖新输入", async f => {
    assert.equal(f.node("kimiKey").value, "");
    f.node("kimiKey").value = "fake-replacement-key";
    f.node("kimiKey").dispatchEvent(new f.dom.window.Event("input"));
    await f.dom.window.refreshState();
    assert.equal(f.node("kimiKey").value, "fake-replacement-key");
    f.dom.window.clearKeyInput();
    await f.dom.window.refreshState();
    assert.equal(f.node("kimiKey").value, "");
  });
  await test("交互修复：手填模型获取列表后保留并支持测试和显式保存", async f => {
    assert.equal(f.node("kimiModel").tagName, "INPUT");
    f.node("kimiModel").value = "manual-model";
    f.node("kimiModel").dispatchEvent(new f.dom.window.Event("input"));
    await f.dom.window.fetchKimiModels();
    assert.equal(f.node("kimiModel").value, "manual-model");
    assert.equal(f.node("kimiModelOptions").querySelectorAll("option").length, 4);
    await f.dom.window.testSelectedModel();
    assert.equal(f.app.calls.find(x => x.route === "/api/model-test").payload.model, "manual-model");
    assert.equal(f.app.calls.filter(x => x.route === "/api/settings" && x.payload).length, 0);
    await f.dom.window.saveSettings();
    assert.equal(f.app.model, "manual-model");
    assert.equal(f.node("kimiKey").value, "");
  });
  await test("交互修复：资料范围始终可见并可调整而不改变分析模板", async f => {
    assert.equal(f.node("targetTags").closest("details"), null);
    f.dom.window.selectScene("open");
    const count = f.app.calls.length;
    [...f.node("targetTags").querySelectorAll("button")].find(b => b.title === "global").click();
    assert.equal(f.app.calls.length, count);
    assert.match(f.node("scopeHint").textContent, /已调整/);
    f.app.available.push("global"); await f.dom.window.refreshState();
    await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.find(x => x.route === "/api/analyze").payload.preset, "open");
    f.node("resetScopeButton").click();
    assert.equal(f.node("targetTags").querySelectorAll('[aria-pressed="true"]').length, 2);
  });
  await test("交互修复：场景入口可直接点击且不触发请求", async f => {
    const buttons = f.dom.window.document.querySelectorAll("[data-scene]");
    assert.equal(buttons.length, 4);
    const count = f.app.calls.length;
    [...buttons].find(b => b.dataset.scene === "intraday").click();
    assert.equal(f.app.calls.length, count);
    assert.equal(f.node("targetTags").querySelectorAll('[aria-pressed="true"]').length, 4);
  });
  await test("交互修复：仅选可用资料是明确操作且保留盘后模板", async f => {
    f.dom.window.selectScene("close");
    f.node("tradeDate").value = "2026-10-19"; await f.dom.window.refreshState();
    assert.match(f.node("workflowSummary").textContent, /仅选可用/);
    f.node("availableScopeButton").click();
    assert.equal(f.node("targetTags").querySelectorAll('[aria-pressed="true"]').length, 2);
    await f.dom.window.runPrimaryAction();
    const request = f.app.calls.find(x => x.route === "/api/analyze");
    assert.equal(request.payload.preset, "close");
    assert.deepEqual(request.payload.targets, ["jingjia", "jjyd"]);
    assert.equal(f.app.calls.filter(x => x.route === "/api/fetch").length, 0);
  });
  await test("历史可用归档不会提示用户点击已禁用的更新按钮", async f => {
    f.node("tradeDate").value = "2026-10-19"; await f.dom.window.refreshState();
    assert.equal(f.node("primaryActionButton").disabled, false);
    assert.equal(f.node("updateDataButton").disabled, true);
    assert.match(f.node("workflowSummary").textContent, /历史归档/);
    assert.doesNotMatch(f.node("workflowSummary").textContent, /可先更新/);
  });
  await test("交互修复：补充关注点保留完整模板和资料并随日期清空", async f => {
    f.node("analysisFocus").value = "重点比较竞价强弱，保留证据。";
    await f.dom.window.runPrimaryAction();
    const request = f.app.calls.find(x => x.route === "/api/analyze");
    assert.match(request.payload.prompt_override, /模拟提示词/);
    assert.match(request.payload.prompt_override, /重点比较竞价强弱/);
    assert.equal(request.payload.preset, "open");
    f.node("tradeDate").value = "2026-10-19"; await f.dom.window.refreshState();
    assert.equal(f.node("analysisFocus").value, "");
  });
  await test("交互修复：关注点生成输入失败不调用模型并解除锁定", async f => {
    f.node("analysisFocus").value = "自定义关注点";
    f.app.hook = route => route === "/api/prompt" ? reply({error:"资料暂不可读"}, 503) : null;
    await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 0);
    assert.match(f.node("statusText").textContent, /资料暂不可读/);
    assert.equal(f.node("analysisFocus").disabled, false);
  });
  await test("交互修复：原始资料展开在刷新后保留", async f => {
    f.node("targetData").querySelector("details").open = true;
    await f.dom.window.refreshState();
    assert.equal(f.node("targetData").querySelector("details").open, true);
  });
  await test("交互修复：编辑后的范围刷新页面仍保留原场景", async f => {
    assert.equal(f.node("sceneSelect").querySelector('[aria-pressed="true"]').dataset.scene, "open");
    assert.match(f.node("scopeHint").textContent, /已调整/);
  }, { storedScene: "open", storedTargets: ["jingjia", "jjyd", "global"] });
  await test("交互修复：清空模型不回退默认也不会发送测试保存或分析", async f => {
    f.node("kimiModel").value = "   ";
    f.node("kimiModel").dispatchEvent(new f.dom.window.Event("input"));
    assert.equal(f.node("primaryActionButton").disabled, true);
    await f.dom.window.testSelectedModel(); await f.dom.window.saveSettings(); await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.filter(x => x.payload).length, 0);
    assert.match(f.node("modelConfigFeedback").textContent, /输入模型/);
  });
  await test("交互修复：新密钥保存成功后清空输入且后续只使用服务端密钥", async f => {
    f.node("kimiKey").value = "fake-new-key";
    f.node("kimiKey").dispatchEvent(new f.dom.window.Event("input"));
    await f.dom.window.saveSettings();
    const saved = f.app.calls.find(x => x.route === "/api/settings" && x.payload);
    assert.equal(saved.payload.kimi_key, "fake-new-key");
    assert.equal(saved.payload.keep_existing_key, false);
    assert.equal(f.node("kimiKey").value, "");
    await f.dom.window.testSelectedModel();
    assert.equal(f.app.calls.find(x => x.route === "/api/model-test").payload.api_key, "");
  });
  await test("交互修复：保存失败保留输入且用户可以继续编辑", async f => {
    f.node("kimiKey").value = "fake-unsaved-key"; f.node("kimiModel").value = "manual-model";
    f.app.hook = (route, payload) => route === "/api/settings" && payload ? reply({error:"配置无法写入"}, 503) : null;
    await f.dom.window.saveSettings();
    assert.equal(f.node("kimiKey").value, "fake-unsaved-key");
    assert.equal(f.node("kimiModel").value, "manual-model");
    assert.equal(f.node("kimiKey").disabled, false);
    assert.match(f.node("modelConfigFeedback").textContent, /保存失败/);
  });
  await test("交互修复：列表完整展示且选择会同步实际使用模型", async f => {
    await f.dom.window.fetchKimiModels();
    const picker = f.node("kimiModelOptions");
    assert.equal(picker.hidden, false);
    picker.value = "model-b"; picker.dispatchEvent(new f.dom.window.Event("change"));
    assert.equal(f.node("kimiModel").value, "model-b");
    assert.match(f.node("currentModelLabel").textContent, /model-b/);
    await f.dom.window.testSelectedModel();
    assert.equal(f.app.calls.find(x=>x.route==="/api/model-test").payload.model, "model-b");
    f.node("kimiKey").value = "different-test-key";
    f.node("kimiKey").dispatchEvent(new f.dom.window.Event("input"));
    assert.equal(picker.hidden, true);
    assert.equal(f.node("kimiModel").value, "model-b");
  });
  await test("交互修复：分析准备期间冻结关注点和场景并阻止重复点击", async f => {
    let finish;
    f.node("analysisFocus").value = "原始关注点";
    f.app.hook = route => route === "/api/prompt" ? new Promise(resolve => { finish = () => resolve(reply({prompt:"完整模板",preset:"open"})); }) : null;
    const task = f.dom.window.runPrimaryAction();
    await until(() => finish);
    assert.equal(f.node("analysisFocus").disabled, true);
    assert.equal(f.node("resetScopeButton").disabled, true);
    assert.ok([...f.node("sceneSelect").querySelectorAll("button")].every(b => b.disabled));
    f.node("sceneSelect").querySelector('[data-scene="close"]').click();
    await f.dom.window.runPrimaryAction();
    finish(); await task;
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
    assert.equal(f.app.calls.find(x => x.route === "/api/analyze").payload.preset, "open");
    assert.equal(f.node("analysisFocus").disabled, false);
  });
  await test("首次载入展示归档状态及原始数据", async f => {
    assert.ok(f.node("targetData").querySelectorAll(".dataset").length > 0);
    assert.match(f.node("targetData").textContent, /10:05/);
    assert.match(f.node("targetData").textContent, /原始证据/);
  });
  await test("场景切换不请求上游且普通重试保留场景", async f => {
    const before = f.app.calls.length; f.scene("open");
    assert.equal(f.app.calls.length, before);
    await f.dom.window.analyzeTargets("selected");
    await f.dom.window.analyzeTargets("selected");
    const calls = f.app.calls.filter(x => x.route === "/api/analyze");
    assert.equal(calls.length, 2);
    assert.ok(calls.every(x => x.payload.preset === "open"));
    assert.deepEqual(calls[0].payload.targets, ["jingjia", "jjyd"]);
  });
  await test("可用资料主操作直接分析且不重复抓取", async f => {
    f.scene("open"); await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.filter(x => x.route === "/api/fetch").length, 0);
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
  });
  await test("过期资料主操作只更新必要模块再分析", async f => {
    f.app.stale = ["jjyd"]; await f.dom.window.refreshState(); f.scene("open");
    assert.match(f.node("workflowSummary").textContent, /先更新.*再进行/);
    await f.dom.window.runPrimaryAction();
    assert.deepEqual(f.app.calls.find(x => x.route === "/api/fetch").payload.targets, ["jjyd"]);
    assert.deepEqual(f.app.calls.find(x => x.route === "/api/analyze").payload.targets, ["jingjia", "jjyd"]);
  });
  await test("更新数据仅采集且保留场景", async f => {
    f.scene("open"); await f.dom.window.fetchTargets("selected");
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 0);
    assert.equal(f.app.calls.find(x => x.route === "/api/prompt").payload.preset, "open");
  });
  await test("临时模型分析不隐式保存", async f => {
    f.scene("open"); await f.dom.window.fetchKimiModels();
    f.node("kimiModel").value = "model-b";
    await f.dom.window.analyzeTargets("selected");
    assert.equal(f.app.calls.filter(x => x.route === "/api/settings" && x.payload).length, 0);
    assert.equal(f.app.calls.find(x => x.route === "/api/analyze").payload.model, "model-b");
  });
  await test("历史结果恢复固定时间和对应模型", async f => {
    await f.dom.window.openAnalysisRecord(f.record.id);
    assert.match(f.node("analysisContent").textContent, /历史结论/);
    assert.match(f.dom.window.buildAnalysisSnapshotTitle().title, /10-20 10:05/);
    assert.match(f.node("analysisMeta").textContent, /model-history/);
  });
  await test("历史日期禁止实时采集", async f => {
    f.scene("intraday"); f.node("tradeDate").value = "2026-10-19";
    await f.dom.window.refreshState();
    assert.equal(f.node("updateDataButton").disabled, true);
    await f.dom.window.fetchTargets("selected");
    assert.equal(f.app.calls.filter(x => x.route === "/api/fetch").length, 0);
  });
  await test("部分采集失败不继续模型调用且可重试失败项", async f => {
    f.app.available = []; f.app.failed = ["jjyd"];
    await f.dom.window.refreshState(); f.scene("open"); await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 0);
    assert.equal(f.node("retryFailedButton").hidden, false);
    f.app.failed = []; await f.dom.window.retryFailedTargets();
    const fetches = f.app.calls.filter(x => x.route === "/api/fetch");
    assert.deepEqual(fetches.at(-1).payload.targets, ["jjyd"]);
  });
  await test("运行中锁定上下文但不遮挡结果且防重复提交", async f => {
    f.scene("open"); f.app.pending = true;
    const work = f.dom.window.analyzeTargets("selected");
    await until(() => f.app.jobs.size === 1);
    assert.ok([...f.node("sceneSelect").querySelectorAll("button")].every(b => b.disabled));
    assert.equal(f.node("tradeDate").disabled, true);
    assert.equal(f.node("loadingMask"), null);
    await f.dom.window.analyzeTargets("selected");
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
    for (const job of f.app.jobs.values()) job.status = "completed";
    await work;
    assert.ok([...f.node("sceneSelect").querySelectorAll("button")].every(b => !b.disabled));
  });
  await test("模块选择支持键盘且旧版重复界面已移除", async f => {
    const tag = f.node("targetTags").firstElementChild;
    assert.equal(tag.tagName, "BUTTON");
    assert.ok(tag.hasAttribute("aria-pressed"));
    assert.equal(f.dom.window.document.querySelectorAll(".legacy-panel").length, 0);
  });
  await test("仅更新另一场景资料不改变现有结论导出标题", async f => {
    await f.dom.window.openAnalysisRecord(f.record.id);
    const title = f.dom.window.buildAnalysisSnapshotTitle().title;
    f.scene("open"); await f.dom.window.fetchTargets("selected");
    assert.equal(f.dom.window.buildAnalysisSnapshotTitle().title, title);
    assert.match(f.node("analysisMeta").textContent, /盘中/);
  });
  await test("快速打开历史记录时旧响应不覆盖后一次选择", async f => {
    let finishFirst;
    f.app.hook = (route, payload, parsed) => route === "/api/analysis" && parsed.searchParams.get("id") === "first"
      ? new Promise(resolve => { finishFirst = () => resolve(reply({ ...f.record, analysis: "第一次旧响应" })); }) : null;
    const first = f.dom.window.openAnalysisRecord("first");
    await until(() => finishFirst);
    await f.dom.window.openAnalysisRecord(f.record.id);
    finishFirst(); await first;
    assert.match(f.node("analysisContent").textContent, /历史结论/);
  });
  await test("日期状态刷新期间禁止基于旧资料发起分析", async f => {
    let finish;
    f.app.hook = route => route === "/api/state" ? new Promise(resolve => {
      finish = () => resolve(reply({ currentDate: "2026-10-19", today: "2026-10-20", availableTargets: [], targetData: [], history: [] }));
    }) : null;
    f.node("tradeDate").value = "2026-10-19";
    const refresh = f.dom.window.refreshState();
    await until(() => finish);
    const finishRefresh = finish;
    const action = f.dom.window.runPrimaryAction();
    await pause();
    const mutations = f.app.calls.filter(x => x.route === "/api/analyze" || x.route === "/api/fetch").length;
    f.app.hook = null;
    finish();
    finishRefresh(); await refresh; await action;
    assert.equal(mutations, 0);
  });
  await test("日期状态乱序返回时保留最后选择的日期", async f => {
    let finish;
    f.app.hook = (route, payload, parsed) => route === "/api/state" && parsed.searchParams.get("date") === "2026-10-19" ? new Promise(resolve => {
      finish = () => resolve(reply({ currentDate: "2026-10-19", today: "2026-10-20", availableTargets: [], targetData: [], history: [] }));
    }) : null;
    f.node("tradeDate").value = "2026-10-19";
    const first = f.dom.window.refreshState();
    f.node("tradeDate").value = "2026-10-20";
    await f.dom.window.refreshState();
    finish(); await first;
    assert.equal(f.node("tradeDate").value, "2026-10-20");
    assert.match(f.node("targetData").textContent, /原始证据/);
  });
  await test("环境变量密钥可用时允许分析且不暴露密钥", async f => {
    f.dom.window.applySettings({ kimiModel: "model-a", hasKimiKey: false, kimiKeyMask: "", hasEffectiveKimiKey: true });
    f.scene("open"); await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
    assert.equal(f.app.calls.find(x => x.route === "/api/analyze").payload.api_key, "");
  });
  await test("旧版标签偏好保留且不会误套盘前场景", async f => {
    assert.equal(f.node("sceneSelect").querySelector('[aria-pressed="true"]').dataset.scene, "default");
    assert.equal(f.node("targetTags").querySelectorAll('[aria-pressed="true"]').length, 2);
  }, { storedTargets: ["global", "amount"] });
  await test("模型列表测试和显式保存完整流程", async f => {
    await f.dom.window.fetchKimiModels();
    f.node("kimiModel").value = "model-b";
    f.node("kimiModel").dispatchEvent(new f.dom.window.Event("input"));
    await f.dom.window.testSelectedModel();
    assert.match(f.node("modelConfigFeedback").textContent, /测试通过/);
    assert.equal(f.app.calls.filter(x => x.route === "/api/settings" && x.payload).length, 0);
    await f.dom.window.saveSettings();
    assert.equal(f.app.model, "model-b");
    assert.equal(f.app.calls.filter(x => x.route === "/api/settings" && x.payload).length, 1);
    assert.equal(f.node("currentModelLabel").textContent, "model-b");
  });
  await test("任务已完成但状态刷新失败仍展示成功结论", async f => {
    f.scene("open");
    f.app.hook = route => route === "/api/state" ? reply({ error: "模拟状态刷新失败" }, 503) : null;
    await f.dom.window.runPrimaryAction();
    assert.match(f.node("analysisContent").textContent, /模拟结论/);
    assert.match(f.node("statusText").textContent, /完成.*刷新/);
    assert.doesNotMatch(f.node("jobLog").textContent, /任务未启动/);
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
  });
  await test("一次任务查询中断会重查原任务而非重发分析", async f => {
    f.scene("open"); let failures = 0;
    f.app.hook = route => route === "/api/job" && failures++ === 0 ? reply({ error: "临时断线" }, 503) : null;
    await f.dom.window.runPrimaryAction();
    assert.match(f.node("analysisContent").textContent, /模拟结论/);
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
  });
  await test("持续查询失败锁定重复提交并可恢复原任务", async f => {
    f.scene("open");
    f.app.hook = route => route === "/api/job" ? reply({ error: "查询暂不可用" }, 503) : null;
    await f.dom.window.runPrimaryAction();
    assert.equal(f.node("primaryActionButton").disabled, true);
    assert.ok(f.node("resumeTaskButton"));
    assert.equal(f.node("resumeTaskButton").hidden, false);
    await f.dom.window.runPrimaryAction();
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
    f.app.hook = null;
    await f.dom.window.resumeActiveJob();
    assert.match(f.node("analysisContent").textContent, /模拟结论/);
    assert.equal(f.node("primaryActionButton").disabled, false);
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
  });
  await test("空选择不触发任务且未来日期不能采集", async f => {
    f.dom.window.selectAllTargets(false);
    await f.dom.window.runPrimaryAction();
    assert.equal(f.node("primaryActionButton").disabled, true);
    f.scene("open"); f.app.available = [];
    f.node("tradeDate").value = "2099-01-01"; await f.dom.window.refreshState();
    assert.equal(f.node("primaryActionButton").disabled, true);
    await f.dom.window.fetchTargets("selected");
    assert.equal(f.app.calls.filter(x => ["/api/analyze", "/api/fetch"].includes(x.route)).length, 0);
  });
  await test("采集查询恢复只跟踪原任务且失败项可单独重试", async f => {
    f.app.available = []; f.app.failed = ["jjyd"];
    await f.dom.window.refreshState(); f.scene("open");
    f.app.hook = route => route === "/api/job" ? reply({ error: "查询断开" }, 503) : null;
    await f.dom.window.runPrimaryAction();
    assert.equal(f.node("primaryActionButton").disabled, true);
    assert.equal(f.app.calls.filter(x => x.route === "/api/fetch").length, 1);
    f.app.hook = null;
    await Promise.all([f.dom.window.resumeActiveJob(), f.dom.window.resumeActiveJob()]);
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 0);
    assert.equal(f.node("retryFailedButton").hidden, false);
    assert.ok([...f.node("sceneSelect").querySelectorAll("button")].every(b => !b.disabled));
    f.app.failed = []; await f.dom.window.retryFailedTargets();
    assert.deepEqual(f.app.calls.filter(x => x.route === "/api/fetch").at(-1).payload.targets, ["jjyd"]);
  });
  await test("服务重启丢失任务时明确说明并解锁且不重发", async f => {
    f.scene("open");
    f.app.hook = route => route === "/api/job" ? reply({ error: "任务不存在" }, 404) : null;
    await f.dom.window.runPrimaryAction();
    assert.match(f.node("statusText").textContent, /记录已不存在.*历史|记录已不存在.*分析记录/);
    assert.ok([...f.node("sceneSelect").querySelectorAll("button")].every(b => !b.disabled));
    assert.equal(f.node("resumeTaskButton").hidden, true);
    assert.equal(f.app.calls.filter(x => x.route === "/api/job").length, 1);
    assert.equal(f.app.calls.filter(x => x.route === "/api/analyze").length, 1);
  });
  await test("Markdown 链接不能插入 HTML 属性", async f => {
    f.dom.window.setAnalysisContent('[属性](https://example.com/\"onmouseover=\"alert%281%29) [数据](data:text/html,test) [编码](javascript&#58;alert%281%29)');
    assert.equal(f.node("analysisContent").querySelectorAll("[onmouseover], [onclick], script").length, 0);
    assert.ok([...f.node("analysisContent").querySelectorAll("a")].every(link => link.protocol === "https:"));
  });
  await test("读取不存在的历史不会覆盖已展示结论", async f => {
    await f.dom.window.openAnalysisRecord(f.record.id);
    f.app.hook = route => route === "/api/analysis" ? reply({ error: "记录不存在" }, 404) : null;
    await assert.rejects(f.dom.window.openAnalysisRecord("missing"), /记录不存在/);
    assert.match(f.node("analysisContent").textContent, /历史结论/);
  });
  await test("资料内容只显示文本且 Markdown 拒绝可执行链接", async f => {
    const malicious = '<img src=x onerror="alert(1)"><script>alert(1)</script>';
    f.dom.window.renderTargetData([{ target: "jingjia", exists: "true", usable: true, content: malicious }]);
    assert.equal(f.node("targetData").querySelectorAll("script,img").length, 0);
    f.dom.window.setAnalysisContent('[危险](javascript:alert%281%29) [正常](https://example.com/info) ' + malicious);
    const links = [...f.node("analysisContent").querySelectorAll("a")];
    assert.equal(links.length, 1);
    assert.equal(links[0].getAttribute("href"), "https://example.com/info");
    assert.equal(f.node("analysisContent").querySelectorAll("script,img").length, 0);
  });
  await test("四种场景默认范围正确且切换本身没有请求", async f => {
    const count = f.app.calls.length;
    for (const [scene, selected] of [["open",2], ["intraday",4], ["close",10]]) {
      f.scene(scene);
      assert.equal(f.node("targetTags").querySelectorAll('[aria-pressed="true"]').length, selected);
    }
    f.scene("default");
    assert.equal(f.app.calls.length, count);
  });
  const log = path.resolve(root, option("--log"));
  assert.ok(log.startsWith(path.join(root, "output") + path.sep));
  fs.mkdirSync(path.dirname(log), { recursive: true });
  fs.writeFileSync(log, JSON.stringify(results, null, 2), "utf8");
  console.log(JSON.stringify(results, null, 2));
  process.exitCode = results.some(x => !x.passed) ? 1 : 0;
})().catch(error => { console.error(error); process.exitCode = 1; });
