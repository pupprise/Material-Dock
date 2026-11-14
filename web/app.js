"use strict";

const $ = (selector, scope = document) => scope.querySelector(selector);
const loginForm = $("#login-form");
const publicEntry = location.protocol === "https:";
const installedRelease = $('meta[name="dock-release"]').content;
$("#public-login-help").hidden = !publicEntry;
let inviteName = "";
let inviteKey = "";
function readInvite() {
  const invite = new URLSearchParams(location.hash.slice(1));
  if (!invite.has("key")) return false;
  inviteName = invite.get("name") || "";
  inviteKey = invite.get("key") || "";
  history.replaceState(null, "", location.pathname + location.search);
  loginForm.elements.name.value = inviteName;
  const notice = $("#login-invite");
  notice.hidden = !inviteName;
  notice.textContent = inviteName ? `此专属链接对应 ${inviteName}` : "";
  return true;
}
readInvite();
const accountDialog = $("#account-dialog");
const accountForm = $("#account-form");
const workspace = $("#workspace");
const login = $("#login");
const dialog = $("#action-dialog");
const form = $("#action-form");
const fields = $("#action-fields");
const formError = $("#action-error");
const submit = $("#action-submit");
const toast = $("#toast");
const itemQuery = new URL(location.href).searchParams.get("item");
const excelKeys = ["style", "purpose", "name", "specification", "color", "quantity", "unit", "requiredBy"];
const excelLabels = ["款号", "用途", "物料", "规格", "颜色", "数量", "单位", "需求到料日"];
const escaped = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
const filters = [
  ["all", "全部", item => ["active", "held"].includes(item.lifecycle)],
  ["urgent", "加急", item => item.urgent && item.lifecycle === "active" && item.state !== "已到齐"],
  ["risk", "交期风险", item => Boolean(item.risk)],
  ["today", "今天应到", item => item.lifecycle === "active" && item.state !== "已到齐" && (item.eta || item.required_by) === data.today],
  ["open", "待联系", item => item.state === "待联系"],
  ["progress", "跟进中", item => ["待到料", "部分到料"].includes(item.state)],
  ["done", "已到料", item => item.state === "已到齐"],
  ["held", "搁置", item => item.lifecycle === "held"],
  ["archived", "撤销 / 作废", item => ["withdrawn", "void"].includes(item.lifecycle)],
];
const workbookType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const followDrafts = new Map();

let data = null;
let selectedId = itemQuery;
let dialogState = null;
let lastSnapshot = "";
let refreshSequence = 0;
let offline = false;
let dockOpen = false;
let toastTimer;
let view = "board";
let filter = "all";
let batch = null;
let importMessage = "";
let importing = false;
let sessionEpoch = 0;
let projectFilter = "";
let requesterFilter = "";
let supplierFilter = "";
let fromFilter = "";
let toFilter = "";
let boardKeyword = "";
const picked = new Map();
const closedStacks = new Set();
let priorityChange = false;
let exporting = false;
let gatewayBlocked = false;
let sessionProbe = 0;
let availableRelease = "";
let owner = true;
let roleName = "设计师";
let accountAction = "";


function e(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => escaped[character]);
}

async function api(path, body) {
  const options = {cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"}, signal: AbortSignal.timeout(18000)};
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const edge = response.headers.get("ngrok-error-code") || "";
  const type = response.headers.get("Content-Type") || "";
  if (edge === "ERR_NGROK_6024") {
    throw serviceError("公网入口的访问确认尚未完成。请重新打开自己的专属链接，核对域名后按页面提示操作。", response.status, "gateway");
  }
  if (response.status === 401 && (response.headers.has("WWW-Authenticate") || !type.includes("application/json"))) {
    throw serviceError("公网入口需要验证。请刷新原专属链接，在浏览器弹窗中填写网关用户名和公网密钥。", response.status, "gateway");
  }
  if (!type.includes("application/json")) {
    const code = /^ERR_NGROK_\d+$/.test(edge) ? `（${edge}）` : `（HTTP ${response.status}）`;
    throw serviceError("没有收到 Dock 的登录响应" + code + "，请重新打开入口后再试。", response.status, edge ? "gateway" : "unexpected_response");
  }
  let result;
  try {
    result = await response.json();
  } catch {
    throw serviceError("Dock 响应不完整，请稍后重试。", response.status, "unexpected_response");
  }
  if (!response.ok) {
    throw serviceError(result.error || "服务暂时无法处理，请重试。", response.status, result.code);
  }
  return result;
}

function serviceError(message, status, code) {
  return Object.assign(new Error(message), {status, code});
}

function loginError(error) {
  if (error.status) return error.message;
  if (publicEntry) return "浏览器没有收到完整的登录响应。若上方有公网验证弹窗，请按分发说明验证，再登录。";
  return "浏览器未收到 Dock 响应，请重新打开内网入口后再试。";
}

async function checkRelease() {
  try {
    const {version} = await api("/versionz");
    return indicateRelease(version);
  } catch {
    return false;
  }
}

function indicateRelease(version) {
  if (typeof version !== "string" || !/^\d+\.\d+\.\d+$/.test(version)) return false;
  availableRelease = version === installedRelease ? "" : version;
  $("#release-message").textContent = availableRelease ? `Dock v${availableRelease} 已上线。` : "";
  $("#release-notice").hidden = !availableRelease;
  return Boolean(availableRelease);
}

$("#reload-release").addEventListener("click", () => {
  if ((dialog.open || accountDialog.open || batch || followDrafts.size) && !window.confirm("当前有未提交的编辑。确认刷新到新版吗？")) return;
  const target = new URL(location.href);
  if (!data && inviteName && inviteKey) target.hash = new URLSearchParams({name: inviteName, key: inviteKey}).toString();
  if (target.href !== location.href) {
    history.replaceState(null, "", target);
  }
  location.reload();
});

function flash(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
}

function pill(label, tone = "") {
  return label ? `<span class="pill ${tone}">${e(label)}</span>` : "";
}

function current() {
  return visibleItems().find(item => item.id === selectedId) ?? null;
}

function visibleItems() {
  const matches = filters.find(([key]) => key === filter)[2];
  return filteredPeople().filter(matches);
}

function filteredPeople() {
  return data?.items.filter(item =>
    (!projectFilter || item.project === projectFilter) &&
    (!requesterFilter || item.requester === requesterFilter) &&
    (!supplierFilter || supplierOf(item) === supplierFilter) &&
    (!fromFilter || item.created_at.slice(0, 10) >= fromFilter) &&
    (!toFilter || item.created_at.slice(0, 10) <= toFilter) &&
    fuzzyMatch(boardKeyword, [item.name, item.project, item.style, item.specification, item.color, item.requester,
      supplierOf(item), item.preferred_supplier, item.request_note, item.state])) || [];
}

function supplierOf(item) {
  return item.supplier || (item.preferred_supplier ?? item.source?.preferredSupplier ?? "");
}

function canFollow(item) {
  return item.lifecycle === "active" && item.state !== "已到齐";
}

function toneOf(item) {
  if (item.lifecycle !== "active") return "paused";
  if (item.state === "已到齐") return "done";
  if (item.risk) return "risk";
  if (item.urgent) return "urgent";
  return "open";
}

function photoUrls(item) {
  const root = `/api/items/${encodeURIComponent(item.id)}/reference`;
  return item.photos?.length ? item.photos.map(index => `${root}/${index}`) : item.has_reference ? [root] : [];
}

function previews(urls, label) {
  return urls.length ? `<span class="photo-strip">${urls.map((url, index) =>
    `<a href="${e(url)}" target="_blank" rel="noopener" title="查看参考图 ${index + 1}"><img alt="${e(label)}参考图 ${index + 1}" src="${e(url)}?thumb=1" loading="lazy"></a>`).join("")}</span>` : "";
}

function syncSelection() {
  const visible = visibleItems();
  if (!visible.some(item => item.id === selectedId)) selectedId = visible[0]?.id ?? null;
}

function urgentNote(note) {
  return note.replace(/加急\s*[:：]\s*(?:否|无|不加急)|(?:无需|不必|不要|暂不|不需|不用|非|无须|取消|不)\s*加急/g, "").includes("加急");
}

function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#${name}"></use></svg>`;
}

function animate(selector, offset) {
  if (reduceMotion.matches) return;
  $(selector, workspace)?.animate(
    [{opacity: .75, transform: `translateY(${offset}px)`}, {opacity: 1, transform: "translateY(0)"}],
    {duration: 210, easing: "cubic-bezier(.22,.61,.36,1)"}
  );
}

function navigation() {
  const tabs = [
    ["board", "queue", owner ? "我的需求" : "跟单看板"],
    ["new", "plus", owner ? "新建需求" : "代下单"],
    ["alerts", "bell", "交期通知"],
  ];
  if (!owner) tabs.splice(2, 0, ["suppliers", "queue", "供应商库"]);
  return `<aside class="side"><nav aria-label="工作台导航">${tabs.map(([id, glyph, label]) =>
    `<button type="button" class="nav${view === id ? " active" : ""}" data-action="view" data-id="${id}"
      ${view === id ? 'aria-current="page"' : ""}>${icon(glyph)}${label}${id === "alerts" && data.unread ? `<span class="count${data.importantUnread ? " important-count" : ""}">${Math.min(99, data.unread)}${data.unread > 99 ? "+" : ""}</span>` : ""}</button>`).join("")}</nav>
    <div class="who"><span class="avatar" aria-hidden="true">${owner ? "项" : "材"}</span><div><b>${roleName}</b><small>双端演示</small></div></div>
  </aside>`;
}

function topBar() {
  return `<header class="top"><div class="brand"><span class="brand-mark">${icon("fabric")}</span>Material Dock</div><div class="top-actions">
    <span class="pilot">v0.5.1 演示 · 独立数据</span>
    <span class="connection${offline ? " offline" : ""}" id="connection">${offline ? "离线 · 上次结果" : "已连接"}</span>
    <span class="role">${e(data.name)} · ${roleName}</span>
    <button type="button" class="quiet" data-action="password">修改密码</button>
    <button type="button" class="quiet" data-action="logout">退出</button>
  </div></header>`;
}

function shortDate(value) {
  return value ? value.slice(5).replace("-", "/") : "—";
}

function identityTags(item) {
  return `<span class="identity-tags"><span class="identity project" title="项目">${e(item.project || "未标注项目")}</span>
    <span class="identity" title="负责人">${e(item.requester || "未标注负责人")}</span></span>`;
}

function designerChoices(selected, none = "其他部门（仅材料员可见）") {
  return `<option value="">${none}</option>` + data.designers.map(name =>
    `<option value="${e(name)}"${selected === name ? " selected" : ""}>${e(name)}</option>`).join("");
}

function selectOptions(items, key, current) {
  const values = [...new Set(items.map(item => item[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  return values.map(value => `<option value="${e(value)}"${value === current ? " selected" : ""}>${e(value)}</option>`).join("");
}

function filterControls() {
  return `<div class="board-controls">
    <label>项目<select data-board-filter="project" aria-label="项目"><option value="">全部项目</option>${selectOptions(data.items, "project", projectFilter)}</select></label>
    ${owner ? "" : `<label>负责人<select data-board-filter="requester" aria-label="负责人"><option value="">全部负责人</option>${selectOptions(data.items, "requester", requesterFilter)}</select></label>`}
    <label>厂家<select data-board-filter="supplier" aria-label="厂家"><option value="">全部厂家</option>${selectOptions(data.items.map(item => ({supplier: supplierOf(item)})), "supplier", supplierFilter)}</select></label>
    <label>提报起日<input type="date" data-board-filter="from" value="${e(fromFilter)}"></label>
    <label>提报止日<input type="date" data-board-filter="to" value="${e(toFilter)}"></label>
    <button class="button" type="button" data-action="clear-filters">清除筛选</button>
    <button class="quiet" type="button" data-action="save-filters">保存常用筛选</button>
    <button class="quiet" type="button" data-action="restore-filters">恢复常用筛选</button>
    ${owner ? "" : `<button class="button export-button" type="button" data-action="export" ${exporting ? "disabled" : ""}>${exporting ? "正在生成…" : "导出当前 Excel"}</button>`}
  </div>`;
}

function resetBoardFilters() {
  filter = "all";
  projectFilter = requesterFilter = supplierFilter = fromFilter = toFilter = boardKeyword = "";
  picked.clear();
}

function boardFilters() {
  return {filter, projectFilter, requesterFilter, supplierFilter, fromFilter, toFilter, boardKeyword};
}

function restoreBoardFilters(saved) {
  filter = filters.some(([key]) => key === saved.filter) ? saved.filter : "all";
  const has = (key, value) => data.items.some(item => item[key] === value) ? value : "";
  projectFilter = has("project", saved.projectFilter);
  requesterFilter = owner ? "" : has("requester", saved.requesterFilter);
  supplierFilter = data.items.some(item => supplierOf(item) === saved.supplierFilter) ? saved.supplierFilter : "";
  fromFilter = saved.fromFilter || "";
  toFilter = saved.toFilter || "";
  boardKeyword = saved.boardKeyword || "";
  picked.clear();
  syncSelection();
  render();
}

function listPanel() {
  const items = visibleItems();
  const stacks = new Map();
  for (const item of items) {
    const id = item.batch_id || item.id;
    if (!stacks.has(id)) stacks.set(id, []);
    stacks.get(id).push(item);
  }
  const rank = ["risk", "urgent", "open", "paused", "done"];
  const rows = [...stacks].map(([id, group]) => {
    const first = group[0];
    const tone = rank.find(value => group.some(item => toneOf(item) === value));
    const label = [...new Set(group.map(item => item.project || "未标注项目"))].join(" / ");
    const requester = [...new Set(group.map(item => item.requester).filter(Boolean))].join(" / ");
    return `<details class="request-stack tone-${tone}" data-stack="${e(id)}" ${closedStacks.has(id) ? "" : "open"}>
      <summary><span><strong>${e(label)}</strong><small>${e(first.created_at.slice(0, 16).replace("T", " "))} · ${e(requester)}</small></span>
      <span class="stack-count">${group.length} 条</span></summary>
      <div class="stack-items">${group.map(item => `<article class="material-row tone-${toneOf(item)}${item.id === selectedId ? " selected" : ""}">
        ${!owner && canFollow(item) ? `<label class="pick-material"><input type="checkbox" data-pick-material="${e(item.id)}" aria-label="勾选 ${e(item.name)}" ${picked.has(item.id) ? "checked" : ""}></label>` : ""}
        <button class="cell-name material-name" type="button" data-action="select" data-id="${e(item.id)}" aria-pressed="${item.id === selectedId}">
          <strong>${e(item.name)}</strong><small>${e(item.style)} · ${e(item.purpose)} · ${e(supplierOf(item) || "待指定厂家")}</small>
        </button>
        ${previews(photoUrls(item), item.name)}
        <span class="material-date"><small>需求 / 预计</small>${e(shortDate(item.required_by))} / ${e(shortDate(item.eta))}</span>
        <span class="state-tags">${item.urgent && canFollow(item) ? pill("加急", "urgent") : ""}${pill(item.risk || item.state, toneOf(item))}</span>
      </article>`).join("")}</div>
    </details>`;
  }).join("");
  const available = filteredPeople();
  const tabs = filters.map(([key, label, matches]) => `<button type="button" class="filter" data-action="filter" data-id="${key}" aria-pressed="${filter === key}">${label}<span>${available.filter(matches).length}</span></button>`).join("");
  const eligible = items.filter(canFollow);
  const selected = eligible.filter(item => picked.has(item.id)).length;
  const bulk = owner ? "" : `<div class="bulk-bar">
    <label><input type="checkbox" data-pick-all aria-label="全选当前筛选" ${eligible.length ? "" : "disabled"} ${selected && selected === eligible.length ? "checked" : ""}>全选当前筛选</label>
    <span class="muted">已选 ${selected} / ${eligible.length} 条可跟进</span>
    <button class="primary" type="button" data-action="bulk" ${selected ? "" : "disabled"}>批量更新跟进</button>
  </div>`;
  return `<section class="queue" id="board" aria-label="${owner ? "我的需求" : "物料跟单看板"}">
    ${filterControls()}
    <div class="filters" aria-label="物料筛选">${tabs}</div>
    <label class="board-search"><span>搜索需求</span><input type="search" id="board-search" maxlength="100" value="${e(boardKeyword)}"
      placeholder="物料、款号、项目、厂家或规格"></label>
    ${bulk}<div class="stacks">${rows || '<p class="empty">当前筛选没有物料。</p>'}</div>
    <div class="queue-foot">显示 ${stacks.size} 轮 · ${items.length} 条物料</div></section>`;
}

function followForm(item) {
  if (owner || !canFollow(item)) return "";
  const draft = followDrafts.get(item.id);
  const names = [...new Set([supplierOf(item), ...data.supplierChoices.map(entry => entry.name)].filter(Boolean))];
  return `<section class="detail-section"><h4>更新跟进</h4>
    <form class="follow-form" data-item="${e(item.id)}" data-version="${draft?.version ?? item.version}">
      <label>供应商<input name="supplier" list="supplier-candidates" maxlength="60" required value="${e(draft?.values.supplier ?? supplierOf(item))}"></label>
      <datalist id="supplier-candidates">${names.map(name => `<option value="${e(name)}"></option>`).join("")}</datalist>
      ${field("eta", "预计到料日", {type: "date", value: draft?.values.eta ?? item.eta})}
      ${field("note", "备注（负责人可见）", {value: draft?.values.note, max: 250, required: false})}
      <p class="error" role="alert">${e(draft?.error)}</p>
      <button class="primary" type="submit" ${draft?.saving ? "disabled" : ""}>${draft?.saving ? "保存中…" : "保存跟进并通知"}</button>
    </form></section>`;
}

function materialIcon(item) {
  const name = item.name || "";
  if (/拉链|拉头|zipper/i.test(name)) return "zipper";
  if (/卡扣|搭扣|纽扣|扣具|暗扣|button|buckle/i.test(name)) return "circle-dashed";
  if (/胶条|织带|包条|包边|松紧带|扎带|tape/i.test(name)) return "ruler-measure";
  if (/吊牌|唛|标签|贴|标识|label/i.test(name)) return "tag";
  if (/面料|里布|布料|纺织|fabric/i.test(name)) return "layers-intersect";
  if (/缝纫|针线|绣|涤纶线|thread/i.test(name)) return "needle-thread";
  return "box";
}

function detailSpecification(value) {
  const text = value || "未填写";
  const number = /((?:\p{Nd}+(?:[.,．]\p{Nd}+)?|[¼½¾⅓⅔⅛⅜⅝⅞])(?:\s*(?:g\/m²|㎜|㎝|[mｍ][mｍ]|[cｃ][mｍ]|[mｍ]\b|[iｉ][nｎ]\b|英寸|毫米|厘米|米|克|"|#|＃|[×xX*](?=\s*\p{Nd})))?)/iu;
  return text.split(number).map((part, index) => index % 2 ? `<strong>${e(part)}</strong>` : e(part)).join("");
}

function detailPanel() {
  const item = current();
  const heading = `<div class="detail-top"><h2>物料详情</h2>${item ? `<small>${e(item.style)} / ${e(item.purpose)}</small>` : ""}</div>`;
  if (!item) return `<aside class="detail" id="details">${heading}<p class="empty">选择一条物料查看详情。</p></aside>`;
  const source = item.source;
  const vendor = supplierOf(item);
  const vendorLabel = item.supplier ? "供应商" : vendor ? "意向供应商" : "供应商";
  const timeline = item.timeline.map(entry => `<article><strong>${e(entry.kind)}</strong><p>${e(entry.message)}</p><small>${e(entry.created_at.slice(0, 16).replace("T", " "))}</small></article>`).join("");
  const notes = [item.pantone && `潘通色号：${item.pantone}`, item.match_sample && "顺料样色", item.vector && "提供矢量图", item.has_reference && "参考样"].filter(Boolean);
  const note = item.request_note || source?.requestNote;
  const files = item.attachments.map(file => `<li><button type="button" class="button" data-action="download-attachment" data-id="${e(file.id)}">${e(file.filename)}</button></li>`).join("");
  const changes = item.lifecycle === "active" ? [["revise", "修改需求"], ["hold", "搁置"], ["withdraw", "撤销"]] :
    item.lifecycle === "held" ? [["revise", "修改需求"], ["resume", "重新激活"], ["withdraw", "撤销"], ["void", "作废"]] :
    item.lifecycle === "withdrawn" ? [["resume", "恢复需求"], ["void", "作废"]] : [];
  return `<aside class="detail tone-${toneOf(item)}" id="details">${heading}<div class="detail-body">
    <div class="material-heading"><span class="material-icon"><img src="/icons/${materialIcon(item)}.svg" alt="" width="26" height="26"></span>
    <div><h3>${e(item.name)}</h3>${item.color ? `<p class="muted">${e(item.color)}</p>` : ""}</div></div>
    <div class="material-measures"><div class="measure-quantity"><small>需求数量</small><div><strong>${e(item.amount)}</strong><span>${e(item.unit)}</span></div></div>
    <div class="measure-spec"><small>规格 / 尺寸</small><p>${detailSpecification(item.specification)}</p></div></div>
    ${identityTags(item)}${item.submitted_by_user ? `<p class="meta muted">下单人 ${e(item.submitted_by_user)}${item.submitted_by === "materials" ? " · 材料员代下单" : ""}</p>` : ""}
    ${owner && !item.project ? '<button type="button" class="button identity-edit" data-action="identity">补填项目</button>' : ""}
    ${!owner && !item.owner_user && !item.submitted_by_user ? '<button type="button" class="button identity-edit" data-action="identity">分配旧记录</button>' : ""}
    <section class="detail-section pair">
      <div><small>需求到料日</small><strong>${e(item.required_by)}</strong></div>
      <div><small>预计到料日</small><strong class="${item.risk && item.eta ? "late" : ""}">${e(item.eta || "待回填")}</strong></div>
    </section>
    <div class="priority-line">${pill(item.risk || item.state, toneOf(item))}
      ${item.urgent && canFollow(item) ? pill("加急", "urgent") : ""}
      ${owner && canFollow(item) ? `<button type="button" class="button" data-action="priority" ${priorityChange ? "disabled" : ""}>${item.urgent ? "取消加急" : "标记加急"}</button>` : ""}</div>
    ${owner && Number(item.received) === 0 && changes.length ? `<div class="request-actions">${changes.map(([action, title]) =>
      `<button class="button" type="button" data-action="${action}">${title}</button>`).join("")}</div>` : ""}
    <section class="detail-section"><h4>${vendorLabel}</h4><p>${e(vendor || "待确认")}</p></section>
    ${notes.length || note ? `<section class="detail-section"><h4>需求备注</h4><div class="actions">${notes.map(tag => pill(tag)).join("")}</div>
      ${note ? `<p class="request-note">${e(note)}</p>` : ""}
      ${previews(photoUrls(item), item.name)}</section>` : ""}
    ${files ? `<section class="detail-section"><h4>矢量图附件</h4><ul class="attachment-list">${files}</ul></section>` : ""}
    ${followForm(item)}
    <section class="detail-section"><h4>收货登记</h4><div class="actions"><p>实收 ${e(item.received)} / ${e(item.amount)} ${e(item.unit)}</p>
    ${!owner && canFollow(item) ? '<button class="button" type="button" data-action="receipt">登记收货</button>' : ""}</div></section>
    ${source ? `<section class="detail-section"><h4>Excel 来源</h4><p>${e(source.file)} · ${e(source.sheet)} 第 ${source.row} 行</p>
      </section>` : ""}
    <section class="detail-section"><h4>跟进记录</h4><div class="timeline" aria-label="跟进记录">${timeline}</div></section>
  </div></aside>`;
}

function alertsPanel() {
  const rows = data.alerts.map(alert => `<button class="notice${alert.read_at ? "" : " unread"}${alert.important ? " important" : ""}" type="button"
    data-action="notice" data-id="${e(alert.item_id)}" data-notice="${alert.id}">
    <span><strong>${e(alert.kind)} · ${e(alert.name)}</strong>${identityTags(alert)}<small>${e(alert.created_at.slice(5, 16).replace("T", " "))}</small></span>
    <span class="notice-flags">${alert.important ? pill("重要", "risk") : ""}${alert.read_at ? "" : pill("未读")}</span>
  </button>`).join("");
  return `<div class="heading"><div><h1>交期通知</h1><p>${data.unread ? `${data.unread} 条未读${data.importantUnread ? ` · ${data.importantUnread} 条重要` : ""}` : "查看交期变更与到料消息。"}</p></div></div>
    <section id="alerts" class="inbox">${rows ? `<div class="notices">${rows}</div>` : '<p class="empty">暂无通知。</p>'}</section>`;
}

function dock() {
  const count = data.unread > 99 ? "99+" : data.unread;
  return `<div class="dock-wrap"><button type="button" class="dock primary" data-action="dock" aria-label="Dock 快捷入口"
    aria-expanded="${dockOpen}" aria-controls="dock-menu">${icon("fabric")}${data.unread ? `<span class="count">${count}</span>` : ""}</button>
    <div id="dock-menu" class="dock-menu" ${dockOpen ? "" : "hidden"}>
      <button type="button" class="button" data-action="view" data-id="${owner ? "new" : "board"}">${icon(owner ? "plus" : "queue")}${owner ? "新建需求" : "跟单看板"}</button>
      <button type="button" class="button" data-action="view" data-id="alerts">${icon("bell")}交期通知${data.unread ? " · " + count : ""}</button>
    </div></div>`;
}

function rowErrors(row) {
  const value = row.values;
  const errors = {};
  for (const [key, name, limit] of [["project", "项目", 80], ["style", "款号", 40], ["name", "物料", 60], ["unit", "单位", 12]]) {
    if (!value[key]?.trim() || value[key].trim().length > limit) errors[key] = `检查${name}`;
  }
  if (!owner && !value.ownerUser && (!value.requester?.trim() || value.requester.trim().length > 40)) errors.requester = "填写其他部门需求人";
  if ((value.specification || "").length > 160) errors.specification = "规格最多 160 字";
  if ((value.color || "").length > 60) errors.color = "颜色最多 60 字";
  if ((value.note || "").length > 512) errors.note = "需求备注最多 512 字";
  if (!["打样", "大货", "补单"].includes(value.purpose)) errors.purpose = "选择用途";
  if (!/^\d{1,8}(?:\.\d{1,4})?$/.test(value.quantity || "") || Number(value.quantity) <= 0) errors.quantity = "确认数量";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value.requiredBy || "")) errors.requiredBy = "确认需求到料日";
  return errors;
}

function rowIssue(row, errors) {
  const notices = Object.values(errors).concat(row.warnings.filter(warning => warning.includes("独立款号")));
  if (urgentNote(row.values.note || "")) notices.push("备注识别为加急");
  return notices.join("、");
}

function importRow(row, index) {
  const errors = rowErrors(row);
  const source = row.source;
  const refs = [`${source.sheet} · 第 ${source.row} 行`];
  if (source.rawQuantity) refs.push("原数量 " + source.rawQuantity);
  if (source.rawDate) refs.push(`原日期（${source.dateLabel}） ${source.rawDate}`);
  if (source.preferredSupplier) refs.push("意向供应商 " + source.preferredSupplier);
  const limits = {style: 40, name: 60, specification: 160, color: 60, quantity: 16, unit: 12};
  const controls = excelKeys.map((key, position) => {
    const value = row.values[key] || "";
    const attrs = `data-import-key="${key}"${errors[key] ? ' class="invalid" aria-invalid="true"' : ""}`;
    const input = key === "purpose"
      ? `<select ${attrs}><option value="">请选择</option>${["打样", "大货", "补单"].map(purpose => `<option${value === purpose ? " selected" : ""}>${purpose}</option>`).join("")}</select>`
      : `<input ${attrs} value="${e(value)}" ${key === "requiredBy" ? 'type="date"' : `maxlength="${limits[key]}"`}${key === "quantity" ? ' inputmode="decimal"' : ""}>`;
    return `<label>${excelLabels[position]}${input}</label>`;
  }).join("");
  const labelFor = (key, label, length) => `<label>${label}<input maxlength="${length}" data-import-key="${key}"
    value="${e(row.values[key])}"${errors[key] ? ' class="invalid" aria-invalid="true"' : ""}></label>`;
  return `<div class="import-row" data-import-row="${index}"><div class="row-line">
    <label class="check-row"><input type="checkbox" data-import-pick ${row.selected ? "checked" : ""} aria-label="${e(`选择 ${source.sheet} 第 ${source.row} 行`)}"></label>
    <span class="source-info">${e(refs.join(" ｜ "))}</span><span class="row-issue">${e(rowIssue(row, errors))}</span>
    ${previews(row.photoIds.map(id => `/api/photos/${id}`), row.values.name)}</div>
    <div class="import-fields">${controls}</div>
    <div class="import-identity">${labelFor("project", "项目", 80)}
      ${owner ? `<label>下单人<input value="${e(data.name)}" readonly></label>` : `<label>归属设计师<select data-import-key="ownerUser">${designerChoices(row.values.ownerUser)}</select></label>${labelFor("requester", "其他部门需求人", 40)}`}
      ${labelFor("note", "需求备注", 512)}
      <label class="tag-option"><input type="checkbox" data-import-key="urgent" ${row.values.urgent ? "checked" : ""}>加急</label>
      <div class="supplier-chooser">${labelFor("preferredSupplier", "意向供应商（可选）", 512)}
        <div class="supplier-suggestions" aria-label="供应商匹配结果" hidden></div></div></div></div>`;
}

function batchPanel() {
  if (!batch) return "";
  const groups = new Map();
  batch.rows.forEach((row, index) => {
    if (!groups.has(row.group)) groups.set(row.group, []);
    groups.get(row.group).push(importRow(row, index));
  });
  const options = batch.defaults;
  return `<div class="import-head"><div><h2>Excel 导入预览</h2><p class="muted">${e(batch.file)} · ${groups.size} 个款号分组 · ${batch.rows.length} 条物料</p></div>
    <button type="button" class="quiet" data-action="discard-import">清空预览</button></div>
    <div class="import-tools" aria-label="批量补全">
      <label>补齐空白项目<input data-default="project" maxlength="80" value="${e(options.project)}"></label>
      ${owner ? `<label>下单人<input value="${e(data.name)}" readonly></label>` :
        `<label>补齐归属设计师<select data-default="ownerUser">${designerChoices(options.ownerUser)}</select></label>
        <label>补齐其他部门需求人<input data-default="requester" maxlength="40" value="${e(options.requester)}"></label>`}
      <label>补齐空白用途<select data-default="purpose"><option value="">请选择</option>${["打样", "大货", "补单"].map(purpose => `<option${options.purpose === purpose ? " selected" : ""}>${purpose}</option>`).join("")}</select></label>
      <label class="date-default">补齐空白需求日<input type="date" data-default="date" value="${e(options.date)}"></label>
      <label class="year">缺失年份<input type="number" min="1900" max="9999" placeholder="年份" data-default="year" value="${e(options.year)}"></label>
      <label class="check"><input type="checkbox" data-default="useOriginal" ${options.useOriginal ? "checked" : ""}>将「收到日期」类原值用作需求日</label>
      <button type="button" class="button" data-action="apply-defaults">补齐所选行</button>
    </div>
    <div id="import-groups">${[...groups].map(([style, rows]) => `<details class="import-group" data-group="${e(style)}" open>
      <summary>${e(style || "未识别款号")} · ${rows.length} 条</summary><div class="import-rows">${rows.join("")}</div></details>`).join("")}</div>
    <div class="import-footer"><span class="muted" id="import-selection" aria-live="polite"></span>
      <button type="button" class="primary" data-action="commit-import">确认并添加需求</button></div>`;
}

function newPanel() {
  return `<div class="heading"><div><h1>${owner ? "新建物料需求" : "代下单"}</h1><p>${owner ? "拖入面辅料 Excel，按款号预览后提交给材料员。" : "为其他负责人或部门提报，填写实际负责人和所属项目。"}</p></div></div>
    <section class="compose" aria-label="需求录入">
      <div class="dropzone" id="dropzone" aria-busy="${importing}"><strong>${importing ? "正在识别工作簿…" : "把 Excel 文件拖到这里"}</strong>
        <span class="muted">.xlsx · 最大 100 MB · 可导入嵌入的参考图</span>
        <div class="actions"><button class="primary" type="button" data-action="pick-file" ${importing ? "disabled" : ""}>选择 Excel 文件</button>
          <button class="button" type="button" data-action="create">${owner ? "快速录入" : "手动代录"}</button>
          <button class="button" type="button" data-action="paste">粘贴一行</button>
          <button class="quiet" type="button" data-action="sample">填入测试物料</button></div>
        <input type="file" id="excel-file" accept=".xlsx,${workbookType}" aria-label="选择 Excel 文件" hidden>
      </div><p id="import-error" class="error" role="alert">${e(importMessage)}</p>${batchPanel()}</section>`;
}

function boardPanel() {
  return `<div class="heading"><div><h1>${owner ? "我的物料需求" : "物料跟单看板"}</h1>
    <p>${owner ? "查看每条物料的预计到料日、实收进度和跟进记录。" : "逐条联系供应商、回填交期，优先处理可能影响项目的物料。"}</p></div>
    ${owner ? `<button class="primary" type="button" data-action="view" data-id="new">${icon("plus")}新建需求</button>`
    : data.importantUnread ? `<button class="button important-shortcut" type="button" data-action="view" data-id="alerts">${icon("bell")}${data.importantUnread} 条重要提醒</button>` : ""}
    </div><div class="columns">${listPanel()}${detailPanel()}</div>`;
}

function render() {
  const active = document.activeElement;
  const activeButton = workspace.contains(active) && active.dataset.action
    ? [active.dataset.action, active.dataset.id ?? "", active.dataset.notice ?? ""]
    : null;
  const activeField = workspace.contains(active) ? {
    follow: active.closest(".follow-form")?.dataset.item, name: active.name,
    row: active.closest("[data-import-row]")?.dataset.importRow, key: active.dataset.importKey,
    defaultKey: active.dataset.default, picked: active.hasAttribute("data-import-pick"),
    boardFilter: active.dataset.boardFilter,
    boardSearch: active.id === "board-search" ? [active.selectionStart, active.selectionEnd] : null,
  } : {};
  const closedGroups = new Set([...workspace.querySelectorAll(".import-group:not([open])")].map(node => node.dataset.group));
  const timeline = $(".timeline", workspace)?.scrollTop ?? 0;
  const page = view === "new" ? newPanel() : view === "alerts" ? alertsPanel() :
    view === "suppliers" && !owner ? supplierPanel() : boardPanel();
  workspace.innerHTML = topBar() + '<div class="layout">' + navigation() + '<div class="main">' + page + "</div></div>" + dock();
  const all = $("[data-pick-all]", workspace);
  if (all) all.indeterminate = !all.checked && visibleItems().some(item => canFollow(item) && picked.has(item.id));
  const nextTimeline = $(".timeline", workspace);
  if (nextTimeline) nextTimeline.scrollTop = timeline;
  for (const group of workspace.querySelectorAll(".import-group")) if (closedGroups.has(group.dataset.group)) group.open = false;
  if (view === "new") updateImportSelection();
  if (activeButton) {
    const restored = [...workspace.querySelectorAll("[data-action]")].find(element => {
      return element.dataset.action === activeButton[0] && (element.dataset.id ?? "") === activeButton[1]
        && (element.dataset.notice ?? "") === activeButton[2];
    });
    restored?.focus({preventScroll: true});
  } else if (activeField.follow && activeField.follow === current()?.id) {
    $(".follow-form", workspace)?.elements.namedItem(activeField.name)?.focus({preventScroll: true});
  } else if (activeField.row !== undefined) {
    const row = $(`[data-import-row="${activeField.row}"]`, workspace);
    if (row && (activeField.picked || activeField.key)) {
      $(activeField.picked ? "[data-import-pick]" : `[data-import-key="${activeField.key}"]`, row)?.focus({preventScroll: true});
    }
  } else if (activeField.defaultKey) {
    $(`[data-default="${activeField.defaultKey}"]`, workspace)?.focus({preventScroll: true});
  } else if (activeField.boardFilter) {
    $(`[data-board-filter="${activeField.boardFilter}"]`, workspace)?.focus({preventScroll: true});
  } else if (activeField.boardSearch) {
    const search = $("#board-search", workspace);
    search?.focus({preventScroll: true});
    search?.setSelectionRange(...activeField.boardSearch);
  }
}

function choose(id, jump = false) {
  const prior = selectedId;
  const previousView = view;
  view = "board";
  selectedId = id;
  const url = new URL(location.href);
  url.searchParams.set("item", id);
  history.replaceState(null, "", url);
  render();
  if (previousView !== view) animate(".main", 7);
  else if (prior !== id) animate(".detail-body", 5);
  if (jump) $("#details").scrollIntoView({block: "nearest", behavior: "auto"});
}

function changeView(next) {
  const changed = view !== next;
  view = next;
  dockOpen = false;
  render();
  if (next === "suppliers" && !owner) loadSupplierDirectory();
  if (changed) {
    animate(".main", 7);
    window.scrollTo(0, 0);
  }
}

function showLogin(message = "") {
  resetBoardFilters();
  closedStacks.clear();
  supplierDirectory = [];
  supplierPreview = [];
  supplierMessage = "";
  supplierBusy = false;
  sessionEpoch++;
  data = null;
  lastSnapshot = "";
  offline = false;
  view = "board";
  filter = "all";
  projectFilter = "";
  requesterFilter = "";
  batch = null;
  followDrafts.clear();
  workspace.hidden = true;
  workspace.replaceChildren();
  login.hidden = false;
  if (dialog.open) dialog.close();
  $("#login-error").textContent = message;
}

async function refresh() {
  const sequence = ++refreshSequence;
  try {
    const fresh = await api("/api/workspace");
    if (sequence !== refreshSequence) return false;
    if (indicateRelease(fresh.release) && data) return true;
    owner = fresh.role === "owner";
    roleName = owner ? "设计师" : "材料员";
    workspace.dataset.role = fresh.role;
    const snapshot = JSON.stringify(fresh);
    const changed = snapshot !== lastSnapshot || offline;
    const knownAlerts = new Set(data?.alerts.map(alert => alert.id));
    const newImportant = !owner && data && fresh.alerts.some(alert => alert.important && !alert.read_at && !knownAlerts.has(alert.id));
    data = fresh;
    gatewayBlocked = false;
    offline = false;
    lastSnapshot = snapshot;
    syncSelection();
    login.hidden = true;
    workspace.hidden = false;
    if (changed) {
      if (view === "new" && $("#dropzone", workspace)) {
        const connection = $("#connection", workspace);
        connection.classList.remove("offline");
        connection.textContent = "已连接";
      } else render();
    }
    if (newImportant) flash("收到加急需求，请查看重要提醒。");
    return true;
  } catch (error) {
    if (sequence !== refreshSequence) return false;
    if (error.code === "gateway") {
      gatewayBlocked = true;
      if (data) {
        offline = true;
        const connection = $("#connection");
        connection.classList.add("offline");
        connection.textContent = "公网入口需要重新验证";
        flash(error.message);
      } else $("#login-error").textContent = error.message;
    } else if (error.status === 401) {
      if (data && !(await checkRelease()) && sequence === refreshSequence) showLogin(error.message);
    } else if (data) {
      offline = true;
      const connection = $("#connection");
      connection.classList.add("offline");
      connection.textContent = "离线 · 上次结果";
    } else {
      $("#login-error").textContent = loginError(error);
    }
    return false;
  }
}

function dateFromToday(days) {
  const date = new Date(data.today + "T00:00:00Z");
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function sample() {
  return {
    style: "试样1", purpose: "打样", name: "前门拉链（样例1）", specification: "5 号 · 60 cm",
    color: "深灰", quantity: "20", unit: "条", requiredBy: dateFromToday(7),
    requester: "样例1", project: "样例1项目",
  };
}

function field(name, label, options = {}) {
  const {value = "", type = "text", max = 60, required = true, wide = false} = options;
  const flag = required ? " required" : "";
  const input = type === "textarea"
    ? `<textarea name="${name}" rows="3" maxlength="${max}"${flag}>${e(value)}</textarea>`
    : `<input name="${name}" type="${type}" maxlength="${max}" value="${e(value)}"${flag}>`;
  return `<label${wide ? ' class="wide"' : ""}>${label}${input}</label>`;
}

function actionForm(action, seed = {}) {
  const item = current();
  let title;
  let button;
  let html;
  const meta = item ? {id: item.id, version: item.version} : {};
  if (action === "create") {
    title = owner ? "新建面辅料需求" : "材料员代下单";
    button = owner ? "提交给材料员" : "提交代下单";
    html = `<label>下单人<input value="${e(data.name)}" readonly></label>`
      + field("project", "所属项目", {value: seed.project, max: 80})
      + (owner ? "" : `<label>归属设计师<select name="ownerUser">${designerChoices(seed.ownerUser)}</select></label>
        <div data-proxy-person ${seed.ownerUser ? "hidden" : ""}>${field("requester", "其他部门需求人", {value: seed.requester, max: 40, required: !seed.ownerUser})}</div>`)
      + field("style", "款号", {value: seed.style, max: 40})
      + `<label>用途<select name="purpose" required>${["打样", "大货", "补单"].map(purpose => `<option${seed.purpose === purpose ? " selected" : ""}>${purpose}</option>`).join("")}</select></label>`
      + field("name", "物料名", {value: seed.name, wide: true})
      + field("specification", "规格", {value: seed.specification, max: 160, required: false})
      + field("color", "颜色", {value: seed.color, required: false})
      + field("quantity", "需求数量", {value: seed.quantity, max: 16})
      + field("unit", "单位", {value: seed.unit, max: 12})
      + field("requiredBy", "需求到料日", {type: "date", value: seed.requiredBy})
      + preferredSupplierField(seed)
      + `<label class="tag-option date-priority"><input name="urgent" type="checkbox"${seed.urgent ? " checked" : ""}>加急 · 通知材料员</label>
        <fieldset class="wide note-options"><legend>备注标签</legend>
          <div class="actions">
            <label class="tag-option"><input type="checkbox" data-action="pantone-toggle">潘通色号</label>
            <label class="tag-option"><input name="matchSample" type="checkbox">顺料样色</label>
            <label class="tag-option"><input name="vector" type="checkbox">提供矢量图</label>
            <button type="button" class="button" data-action="pick-reference">参考样（插入图片）</button>
          </div>
          <div data-pantone hidden>${field("pantone", "潘通色号", {max: 60, required: false})}</div>
          <input id="vector-files" type="file" multiple aria-label="矢量图附件" hidden>
          <p class="muted" id="vector-status" aria-live="polite" hidden></p>
          <ul class="attachment-list" id="vector-list" hidden></ul>
          <input id="reference-file" type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/bmp,.bmp" aria-label="参考样图片" hidden>
          <div id="reference-preview" class="reference-preview" hidden><img alt="参考样预览"><button type="button" class="quiet" data-action="remove-reference">移除图片</button></div>
        </fieldset>`
      + field("note", "需求备注", {value: seed.note, max: 512, type: "textarea", required: false, wide: true})
      + '<p id="note-priority" class="wide note-priority" role="status" hidden>备注识别为加急，材料员会收到重要提醒。</p>';
  } else if (action === "supplier-edit" && !owner) {
    const entry = supplierDirectory.find(record => record.id === seed.id);
    if (seed.id && !entry) return;
    if (entry) meta.supplierId = entry.id;
    title = entry ? "编辑供应商" : "新增供应商";
    button = "保存供应商";
    html = supplierFields.map(([name, label, max]) => field(name, label, {
      max, value: entry?.[name], required: name === "name", wide: ["name", "address", "note"].includes(name),
      type: name === "note" ? "textarea" : "text",
    })).join("");
  } else if (action === "bulk" && !owner) {
    meta.items = visibleItems().filter(item => canFollow(item) && picked.has(item.id)).map(item => ({
      id: item.id, version: picked.get(item.id),
    }));
    if (!meta.items.length) return;
    title = "批量更新跟进";
    button = "保存并通知";
    html = `<p class="wide muted">将更新 ${meta.items.length} 条物料。供应商和预计到料日留空时沿用原值。</p>`
      + field("supplier", "供应商（可选）", {required: false})
      + field("eta", "预计到料日（可选）", {type: "date", required: false})
      + field("note", "最新说明（设计师可见）", {type: "textarea", max: 250, wide: true});
  } else if (action === "revise" && owner && item) {
    title = "修改需求";
    button = "保存修改并通知";
    html = field("project", "所属项目", {value: item.project, max: 80})
      + field("style", "款号", {value: item.style, max: 40})
      + field("name", "物料名", {value: item.name, wide: true})
      + field("specification", "规格", {value: item.specification, max: 160, required: false})
      + field("color", "颜色", {value: item.color, required: false})
      + field("quantity", "需求数量", {value: item.amount, max: 16})
      + field("unit", "单位", {value: item.unit, max: 12})
      + field("requiredBy", "需求到料日", {type: "date", value: item.required_by})
      + field("note", "需求备注", {value: item.request_note, max: 512, type: "textarea", required: false, wide: true})
      + field("reason", "修改原因", {max: 250, type: "textarea", wide: true});
  } else if (["hold", "resume", "withdraw", "void"].includes(action) && owner && item) {
    meta.change = action;
    title = {hold: "搁置需求", resume: "重新激活需求", withdraw: "撤销需求", void: "作废需求"}[action];
    button = "确认" + title;
    html = `<p class="wide muted">${e(item.name)}${action === "void" ? " · 作废后移至归档并保留历史。" : ""}</p>`
      + field("reason", "原因", {max: 250, type: "textarea", wide: true});
  } else if (action === "paste") {
    title = "粘贴 Excel 数据行";
    button = "预览并修正";
    html = `<p class="wide muted">${excelLabels.join(" / ")}</p>`
      + field("excel", "从 Excel 复制上述顺序的一行", {type: "textarea", max: 4096, wide: true})
      + '<div class="wide"><button type="button" data-action="paste-sample">填入试点样例</button></div>';
  } else if (action === "identity" && item && (owner || (!item.owner_user && !item.submitted_by_user))) {
    title = owner ? "补填项目" : "分配旧记录";
    button = "保存归属";
    html = (owner ? `<label>归属设计师<input value="${e(data.name)}" readonly></label>` :
      `<label>归属设计师<select name="ownerUser" required>${designerChoices("", "请选择设计师")}</select></label>`)
      + field("project", "所属项目", {value: item.project, max: 80});
  } else if (action === "receipt" && item && item.state !== "已到齐") {
    title = "登记收货";
    button = "保存实收";
    html = `<p class="wide muted">${e(item.name)} · 已收 ${e(item.received)} / ${e(item.amount)} ${e(item.unit)}</p>`
      + field("quantity", `本次实收（${e(item.unit)}）`, {max: 16})
      + field("receivedOn", "收货日期", {type: "date", value: data.today})
      + field("note", "收货备注", {type: "textarea", max: 250, required: false, wide: true});
  } else {
    return;
  }
  dialogState = {action, meta, key: "", signature: "", attachments: []};
  $("#action-title").textContent = title;
  submit.textContent = button;
  fields.innerHTML = `<div class="field-grid">${html}</div>`;
  formError.textContent = "";
  if (!dialog.open) dialog.showModal();
  if (action === "create") $("#note-priority").hidden = !urgentNote(seed.note || "");
  fields.querySelector("input, textarea")?.focus();
}

function operationKey() {
  return [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function writeKey(draft, values) {
  const signature = JSON.stringify(values);
  if (signature !== draft.signature) {
    draft.signature = signature;
    draft.key = operationKey();
  }
  return draft.key;
}

function readFollow(formElement) {
  const id = formElement.dataset.item;
  const draft = followDrafts.get(id) || {version: Number(formElement.dataset.version), key: "", signature: ""};
  draft.values = Object.fromEntries(new FormData(formElement));
  followDrafts.set(id, draft);
  return draft;
}

async function saveFollow(event) {
  const editor = event.target.closest(".follow-form");
  if (!editor) return;
  event.preventDefault();
  const id = editor.dataset.item;
  const draft = readFollow(editor);
  if (draft.saving) return;
  draft.saving = true;
  draft.error = "";
  const payload = {id, version: draft.version, ...draft.values};
  const operationId = writeKey(draft, payload);
  render();
  try {
    await api("/api/follow-up", {...payload, operationId});
    followDrafts.delete(id);
    await refresh();
    render();
    flash("交期已保存。");
  } catch (error) {
    draft.saving = false;
    draft.error = error.status ? error.message : "连接中断。恢复后可重试。";
    if (error.status === 409) {
      await refresh();
      const latest = data?.items.find(item => item.id === id);
      if (latest) {
        draft.version = latest.version;
        draft.error = "物料已更新，请核对右侧最新记录，再确认保存当前填写的内容。";
      }
    }
    if (data) render();
  }
}

function showImportMessage(message) {
  importMessage = message;
  const output = $("#import-error", workspace);
  if (output) output.textContent = message;
}

function updateImportSelection() {
  const summary = $("#import-selection", workspace);
  if (!batch || !summary) return;
  const selected = batch.rows.filter(row => row.selected);
  const unresolved = selected.filter(row => Object.keys(rowErrors(row)).length).length;
  summary.textContent = `已选 ${selected.length} 条${unresolved ? ` · ${unresolved} 条待修正` : ""}`;
  const button = $('[data-action="commit-import"]', workspace);
  button.disabled = batch.committing || !selected.length || Boolean(unresolved);
  button.textContent = batch.committing ? "正在添加…" : "确认并添加需求";
}

async function loadExcel(files) {
  if (!data || importing || batch?.committing || !files?.length) return;
  if (view !== "new") changeView("new");
  if (files.length !== 1) {
    showImportMessage("每次拖入一个 .xlsx 工作簿。");
    return;
  }
  const [file] = files;
  if (!file.name.toLowerCase().endsWith(".xlsx") || !file.size || file.size > 100 * 1024 * 1024) {
    showImportMessage("请选择不超过 100 MB 的 .xlsx 文件。旧版 .xls 可在 Excel 里另存为 .xlsx。");
    return;
  }
  importing = true;
  batch = null;
  const epoch = sessionEpoch;
  showImportMessage("");
  render();
  try {
    const response = await fetch("/api/import/preview", {
      method: "POST", headers: {"Content-Type": workbookType, "X-Dock-Filename": encodeURIComponent(file.name)},
      body: file, cache: "no-store", signal: AbortSignal.timeout(600000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "这份工作簿无法识别。");
    if (epoch !== sessionEpoch) return;
    batch = {
      file: result.file,
      rows: result.rows.map(row => ({
        ...row, group: row.values.style, selected: true,
        values: {
          ...row.values, requester: owner ? data.name : row.values.requester, preferredSupplier: row.source.preferredSupplier || "",
          ownerUser: !owner && data.designers.includes(row.values.requester) ? row.values.requester : "",
        },
      })),
      defaults: {project: "", requester: "", ownerUser: "", purpose: "", date: "", year: "", useOriginal: false},
      committing: false,
    };
  } catch (error) {
    if (epoch === sessionEpoch) showImportMessage(error instanceof TypeError ? "连接中断，工作簿未提交。请再拖入一次。" : error.message);
  } finally {
    importing = false;
    if (data) render();
    if (batch && view === "new") animate("#import-groups", 5);
  }
}

function dateWithYear(raw, year) {
  const full = raw.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/);
  const partial = raw.match(/^(\d{1,2})[-/.月](\d{1,2})日?$/);
  const parts = full?.slice(1) || (partial && /^\d{4}$/.test(year) ? [year, ...partial.slice(1)] : null);
  if (!parts) return "";
  const result = parts.map((value, index) => value.padStart(index === 0 ? 4 : 2, "0")).join("-");
  const date = new Date(result + "T00:00:00Z");
  return Number.isFinite(date.valueOf()) && date.toISOString().slice(0, 10) === result ? result : "";
}

function applyDefaults() {
  if (!batch) return;
  const options = batch.defaults;
  for (const row of batch.rows.filter(item => item.selected)) {
    const value = row.values;
    const source = row.source;
    if (!value.purpose && options.purpose) value.purpose = options.purpose;
    if (!value.project?.trim()) value.project = options.project.trim();
    if (!owner && !value.ownerUser && !value.requester?.trim()) {
      value.ownerUser = options.ownerUser;
      value.requester = options.requester.trim();
    }
    if (!value.requiredBy) {
      const canUseSource = source.dateLabel === "需求到料日" || options.useOriginal;
      value.requiredBy = (canUseSource && source.rawDate ? dateWithYear(source.rawDate, options.year) : "") || options.date;
    }
  }
  showImportMessage("");
  render();
}

async function commitImport() {
  const draft = batch;
  if (!draft || draft.committing) return;
  const selected = draft.rows.filter(row => row.selected);
  if (!selected.length || selected.some(row => Object.keys(rowErrors(row)).length)) return;
  const rows = selected.map(row => ({...row.values, photoIds: row.photoIds, source: {...row.source, file: draft.file}}));
  const operationId = writeKey(draft, rows);
  draft.committing = true;
  showImportMessage("");
  updateImportSelection();
  try {
    const result = await api("/api/import/commit", {rows, operationId});
    batch = null;
    resetBoardFilters();
    selectedId = result.id;
    await refresh();
    if (data) choose(result.id);
    flash(`已添加 ${result.count} 条物料需求。`);
  } catch (error) {
    showImportMessage(error.status ? error.message : "连接中断。恢复后点击「确认并添加需求」可安全重试。");
  } finally {
    draft.committing = false;
    updateImportSelection();
  }
}

function clearReference(editing = dialogState) {
  if (!editing) return;
  if (editing.previewUrl) URL.revokeObjectURL(editing.previewUrl);
  editing.previewUrl = "";
  editing.referenceId = "";
  editing.imageSequence = (editing.imageSequence || 0) + 1;
  const preview = $("#reference-preview", fields);
  if (preview) {
    preview.hidden = true;
    $("img", preview).removeAttribute("src");
  }
}

async function chooseReference(file) {
  const editing = dialogState;
  if (!file || editing?.action !== "create" || submit.disabled) return;
  if (!/\.(?:jpe?g|png|webp|gif|bmp)$/i.test(file.name) && !/^image\/(?:jpeg|png|webp|gif|bmp|x-ms-bmp)$/.test(file.type)) {
    formError.textContent = "请选择 JPG、PNG、WebP、GIF 或 BMP 图片。";
    return;
  }
  const sequence = (editing.imageSequence || 0) + 1;
  editing.imageSequence = sequence;
  formError.textContent = "";
  submit.disabled = true;
  try {
    const response = await fetch("/api/photos/stage", {
      method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "图片上传失败。");
    if (dialogState !== editing || editing.imageSequence !== sequence || !dialog.open) return;
    if (editing.previewUrl) URL.revokeObjectURL(editing.previewUrl);
    editing.previewUrl = URL.createObjectURL(file);
    editing.referenceId = result.id;
    const preview = $("#reference-preview", fields);
    $("img", preview).src = editing.previewUrl;
    preview.hidden = false;
  } catch (error) {
    if (dialogState === editing) formError.textContent = error.message || "无法读取这张参考样，请重新选择。";
  } finally {
    if (dialogState === editing) submit.disabled = false;
  }
}

function discardFiles(files) {
  if (files.length) api("/api/attachments/discard", {ids: files.map(file => file.id)}).catch(() => {});
}

function paintVectorStatus(editing, message = "") {
  const checkbox = form.elements.namedItem("vector");
  const list = $("#vector-list", fields);
  const status = $("#vector-status", fields);
  if (!checkbox || !list || !status || editing !== dialogState) return;
  checkbox.checked = Boolean(editing.attachments.length || editing.uploading);
  list.hidden = !editing.attachments.length;
  list.innerHTML = editing.attachments.map(file => `<li><span>${e(file.filename)}</span>
    <button type="button" class="quiet" data-action="remove-vector" data-id="${e(file.id)}">移除</button></li>`).join("");
  status.hidden = !message;
  status.textContent = message;
}

async function stageVector(files) {
  const editing = dialogState;
  if (editing?.action !== "create" || editing.uploading || submit.disabled) return;
  if (!files.length) {
    paintVectorStatus(editing);
    return;
  }
  if (editing.attachments.length + files.length > 8 || files.some(file => file.size > 20 * 1024 * 1024)
      || [...editing.attachments, ...files].reduce((size, file) => size + file.size, 0) > 60 * 1024 * 1024) {
    paintVectorStatus(editing);
    formError.textContent = "附件最多 8 个，单个最多 20 MB，合计最多 60 MB。";
    return;
  }
  editing.uploading = true;
  submit.disabled = true;
  formError.textContent = "";
  paintVectorStatus(editing, "正在上传附件…");
  try {
    for (const file of files) {
      const response = await fetch("/api/attachments/stage", {
        method: "POST", body: file, signal: AbortSignal.timeout(120000),
        headers: {"Content-Type": "application/octet-stream", "X-Dock-Filename": encodeURIComponent(file.name)},
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "上传失败。");
      if (dialogState !== editing || !dialog.open) {
        discardFiles([result]);
        return;
      }
      editing.attachments.push(result);
    }
  } catch (error) {
    if (dialogState === editing) formError.textContent = error.message || "附件未能上传。";
  } finally {
    editing.uploading = false;
    if (dialogState === editing) {
      submit.disabled = false;
      paintVectorStatus(editing, editing.attachments.length ? `已上传 ${editing.attachments.length} 个附件` : "");
    }
  }
}

function saveFile(encoded, filename, mime) {
  const blob = new Blob([Uint8Array.from(atob(encoded), character => character.charCodeAt(0))], {type: mime});
  const url = URL.createObjectURL(blob);
  const download = document.createElement("a");
  download.href = url;
  download.download = filename;
  document.body.append(download);
  download.click();
  download.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function downloadAttachment(id) {
  try {
    const response = await fetch("/api/attachments/" + encodeURIComponent(id), {
      headers: {Accept: "application/json"}, cache: "no-store", signal: AbortSignal.timeout(120000),
    });
    const result = await response.json();
    if (!response.ok || typeof result.file !== "string") throw new Error(result.error || "附件无法读取。");
    saveFile(result.file, result.filename, "application/octet-stream");
  } catch (error) {
    flash(error.message || "附件下载失败。");
  }
}

async function togglePriority() {
  const item = current();
  if (!owner || !item || priorityChange) return;
  priorityChange = true;
  render();
  try {
    await api("/api/priority", {id: item.id, version: item.version, urgent: !item.urgent, operationId: operationKey()});
    await refresh();
    flash(item.urgent ? "已取消加急。" : "已标为加急，材料员将收到重要提醒。");
  } catch (error) {
    await refresh();
    flash(error.status ? error.message : "暂时无法更新加急状态。");
  } finally {
    priorityChange = false;
    if (data) render();
  }
}

async function exportCurrent() {
  if (owner || !data || exporting) return;
  const params = new URLSearchParams({status: filter, project: projectFilter, requester: requesterFilter,
    supplier: supplierFilter, from: fromFilter, to: toFilter, q: boardKeyword});
  exporting = true;
  render();
  try {
    const response = await fetch("/api/export?" + params, {headers: {Accept: "application/json"}, cache: "no-store", signal: AbortSignal.timeout(30000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "暂时无法导出。");
    if (!result.xlsx || !result.filename) throw new Error("没有收到有效的 Excel 文件，请重新导出。");
    saveFile(result.xlsx, result.filename, workbookType);
  } catch (error) {
    flash(error instanceof TypeError ? "连接中断，请稍后重新导出。" : error.message);
  } finally {
    exporting = false;
    if (data) render();
  }
}

async function submitAction(event) {
  event.preventDefault();
  if (!dialogState || submit.disabled) return;
  const editing = dialogState;
  const values = Object.fromEntries(new FormData(form));
  formError.textContent = "";
  if (editing.action === "supplier-edit") {
    submit.disabled = true;
    const epoch = sessionEpoch;
    try {
      await api("/api/suppliers/save", {...values, ...(editing.meta.supplierId ? {id: editing.meta.supplierId} : {}),
        operationId: writeKey(editing, values)});
      if (epoch !== sessionEpoch) return;
      dialog.close();
      supplierMessage = "供应商已保存。";
      await loadSupplierDirectory();
      await refresh();
    } catch (error) {
      if (epoch === sessionEpoch) formError.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
    return;
  }
  if (editing.action === "paste") {
    const lines = values.excel.trim().split(/\r?\n/).filter(line => line.trim());
    if (lines[0]?.split("\t")[0].trim() === "款号") lines.shift();
    const parts = lines[0]?.split("\t").map(value => value.trim());
    if (lines.length !== 1 || parts?.length !== excelKeys.length || !["打样", "大货", "补单"].includes(parts[1])) {
      formError.textContent = "请按给出的顺序粘贴一条含 8 列的数据行，用途填打样、大货或补单。";
      return;
    }
    actionForm("create", Object.fromEntries(excelKeys.map((key, index) => [key, parts[index]])));
    return;
  }
  if (editing.action === "create") {
    for (const key of ["urgent", "matchSample"]) values[key] = form.elements.namedItem(key).checked;
    delete values.vector;
    values.attachmentIds = editing.attachments.map(file => file.id);
    if (!$("[data-action='pantone-toggle']", form).checked) values.pantone = "";
    if (editing.referenceId) values.photoIds = [editing.referenceId];
  }
  const urls = {create: "/api/items", receipt: "/api/receipt", identity: "/api/identity",
    bulk: "/api/follow-up/batch", revise: "/api/items/revise",
    hold: "/api/items/lifecycle", resume: "/api/items/lifecycle", withdraw: "/api/items/lifecycle", void: "/api/items/lifecycle"};
  const message = {create: "需求已提交给材料员。", receipt: "实收已登记。", identity: "项目与负责人已保存。"};
  const body = {...(editing.action === "create" ? {} : editing.meta), ...values, operationId: writeKey(editing, values)};
  submit.disabled = true;
  try {
    const result = await api(urls[editing.action], body);
    if (dialog.open) dialog.close();
    selectedId = result.id;
    if (editing.action === "create") {
      resetBoardFilters();
    }
    if (editing.action === "bulk") picked.clear();
    if (["withdraw", "void"].includes(editing.action)) filter = "archived";
    if (editing.action === "resume") filter = "all";
    await refresh();
    if (visibleItems().some(item => item.id === result.id)) choose(result.id);
    flash(editing.action === "create" && !owner ? "代下单已提交。" :
      editing.action === "bulk" ? `已更新 ${result.count} 条物料。` : message[editing.action] || "需求状态或内容已更新。");
  } catch (error) {
    formError.textContent = error.status ? error.message : "连接中断。恢复后可用当前表单重试。";
  } finally {
    submit.disabled = false;
  }
}

function accountPanel(mode, name = "") {
  accountAction = mode;
  const recovery = mode === "recover";
  $("#account-title").textContent = recovery ? "忘记密码" : "修改密码";
  $("#account-help").textContent = recovery
    ? "验证个人专属链接中的密钥后，密码会恢复为初始密码。"
    : "请输入当前密码并设置新密码。";
  $("#account-fields").innerHTML = `<div class="field-grid">${recovery
    ? field("name", "姓名", {value: name || loginForm.elements.name.value, max: 40, wide: true})
      + field("key", "个人恢复密钥", {type: "password", value: name === inviteName ? inviteKey : "", max: 96, wide: true})
      + '<label class="wide tag-option"><input name="rememberDevice" type="checkbox">记住这台个人设备 30 天</label>'
    : `<label class="wide">当前密码<input type="password" name="currentPassword" autocomplete="current-password" maxlength="64" required></label>
       <label class="wide">新密码<input type="password" name="newPassword" autocomplete="new-password" minlength="8" maxlength="64" required></label>
       <label class="wide">确认新密码<input type="password" name="confirmPassword" autocomplete="new-password" minlength="8" maxlength="64" required></label>`
  }</div>`;
  $("#account-error").textContent = "";
  $("#account-submit").textContent = recovery ? "验证并重置" : "保存密码";
  if (!accountDialog.open) accountDialog.showModal();
}

function endAccountPanel() {
  if ($("#account-submit").disabled) return;
  accountDialog.close();
}

accountForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#account-submit");
  if (button.disabled) return;
  const values = Object.fromEntries(new FormData(accountForm));
  const error = $("#account-error");
  error.textContent = "";
  if (accountAction !== "recover" && values.newPassword !== values.confirmPassword) {
    error.textContent = "两次输入的新密码不一致。";
    return;
  }
  button.disabled = true;
  try {
    if (accountAction === "recover") {
      const account = await api("/api/recover", {name: values.name, key: values.key, rememberDevice: values.rememberDevice === "on"});
      loginForm.elements.name.value = account.name;
      loginForm.elements.password.value = "";
      accountDialog.close();
      accountForm.reset();
      await refresh();
      flash("密码已恢复为初始密码。");
    } else {
      await api("/api/password", {currentPassword: values.currentPassword, newPassword: values.newPassword});
      accountDialog.close();
      accountForm.reset();
      await refresh();
      flash("个人密码已更新。");
    }
  } catch (problem) {
    error.textContent = loginError(problem);
  } finally {
    button.disabled = false;
  }
});

$("#forgot-password").addEventListener("click", () => accountPanel("recover", loginForm.elements.name.value || inviteName));
$("#account-close").addEventListener("click", endAccountPanel);
accountDialog.addEventListener("cancel", event => {
  event.preventDefault();
  endAccountPanel();
});

loginForm.addEventListener("submit", async event => {
  event.preventDefault();
  const loginForm = event.currentTarget;
  const button = loginForm.querySelector("button");
  const {name, password, rememberDevice} = Object.fromEntries(new FormData(loginForm));
  if (inviteName && name.trim() !== inviteName) {
    $("#login-error").textContent = `这是 ${inviteName} 的专属链接。请打开本人姓名对应的链接。`;
    return;
  }
  ++sessionProbe;
  button.disabled = true;
  try {
    await api("/api/login", {name, password, key: name.trim() === inviteName ? inviteKey : "", rememberDevice: rememberDevice === "on"});
    loginForm.elements.password.value = "";
    $("#login-error").textContent = "";
    await refresh();
  } catch (error) {
    $("#login-error").textContent = loginError(error);
  } finally {
    button.disabled = false;
  }
});

form.addEventListener("submit", submitAction);
form.addEventListener("input", event => {
  const input = event.target;
  if (input.name === "preferredSupplier") {
    form.elements.preferredSupplierId.value = "";
    showSupplierMatches(input);
  }
  if (input.name === "ownerUser" && dialogState?.action === "create") {
    const proxy = $("[data-proxy-person]", form);
    proxy.hidden = Boolean(input.value);
    form.elements.namedItem("requester").required = !input.value;
  }
  if (input.name === "vector" && dialogState?.action === "create") {
    if (input.checked) {
      const picker = $("#vector-files", fields);
      picker.value = "";
      picker.click();
    } else {
      discardFiles(dialogState.attachments);
      dialogState.attachments = [];
      paintVectorStatus(dialogState);
    }
  }
  if (input.dataset.action === "pantone-toggle") {
    const panel = $("[data-pantone]", form);
    panel.hidden = !input.checked;
    form.elements.namedItem("pantone").required = input.checked;
    if (input.checked) form.elements.namedItem("pantone").focus();
  }
  if (input.name === "note" && dialogState?.action === "create") $("#note-priority").hidden = !urgentNote(input.value);
});
form.addEventListener("change", event => {
  if (event.target.id === "reference-file") chooseReference(event.target.files[0]);
  if (event.target.id === "vector-files") stageVector([...event.target.files]);
});
form.addEventListener("cancel", event => {
  if (event.target.id === "vector-files" && dialogState) paintVectorStatus(dialogState);
}, true);
form.addEventListener("paste", event => {
  if (dialogState?.action !== "create") return;
  const image = [...event.clipboardData.files].find(file => file.type.startsWith("image/"));
  if (image) {
    event.preventDefault();
    chooseReference(image);
  }
});
dialog.addEventListener("cancel", event => {
  if (submit.disabled) event.preventDefault();
});
dialog.addEventListener("close", () => {
  if (!dialog.open) {
    clearReference();
    discardFiles(dialogState?.attachments || []);
  }
});
dialog.addEventListener("click", event => {
  const option = event.target.closest("[data-supplier-option]");
  if (option) {
    const entry = data?.supplierChoices.find(record => record.id === option.dataset.supplierOption);
    if (entry) {
      form.elements.preferredSupplier.value = entry.name;
      form.elements.preferredSupplierId.value = entry.id;
      form.elements.preferredSupplier.focus();
      $(".supplier-suggestions", form).hidden = true;
    }
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "close" && !submit.disabled) dialog.close();
  if (action === "pick-reference" && !submit.disabled) {
    const file = $("#reference-file", fields);
    file.value = "";
    file.click();
  }
  if (action === "remove-reference") clearReference();
  if (action === "remove-vector" && dialogState && !dialogState.uploading) {
    const id = event.target.closest("button").dataset.id;
    discardFiles(dialogState.attachments.filter(file => file.id === id));
    dialogState.attachments = dialogState.attachments.filter(file => file.id !== id);
    paintVectorStatus(dialogState, dialogState.attachments.length ? `已上传 ${dialogState.attachments.length} 个附件` : "");
  }
  if (action === "paste-sample") {
    const demo = sample();
    form.elements.excel.value = excelKeys.map(key => demo[key]).join("\t");
  }
});

workspace.addEventListener("submit", saveFollow);
function setBoardSearch(input) {
  if (input.id !== "board-search" || boardKeyword === input.value) return;
  boardKeyword = input.value;
  picked.clear();
  syncSelection();
  render();
}

workspace.addEventListener("compositionend", event => setBoardSearch(event.target));
workspace.addEventListener("input", event => {
  const input = event.target;
  if (input.id === "board-search") {
    if (!event.isComposing) setBoardSearch(input);
    return;
  }
  const follow = input.closest(".follow-form");
  if (follow) readFollow(follow);
  if (batch && input.dataset.default) batch.defaults[input.dataset.default] = input.type === "checkbox" ? input.checked : input.value;
  const rowElement = input.closest("[data-import-row]");
  if (!batch || !rowElement) return;
  const row = batch.rows[Number(rowElement.dataset.importRow)];
  if (input.hasAttribute("data-import-pick")) {
    row.selected = input.checked;
  } else if (input.dataset.importKey) {
    const key = input.dataset.importKey;
    row.values[key] = input.type === "checkbox" ? input.checked : input.value;
    if (key === "preferredSupplier") {
      row.values.preferredSupplierId = "";
      showSupplierMatches(input);
    }
    const errors = rowErrors(row);
    input.classList.toggle("invalid", Boolean(errors[key]));
    if (errors[key]) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
    $(".row-issue", rowElement).textContent = rowIssue(row, errors);
  }
  updateImportSelection();
});
workspace.addEventListener("change", event => {
  const input = event.target;
  if (input.id === "supplier-file") loadSupplierWorkbook(input.files[0]);
  if (input.dataset.supplierPick !== undefined && supplierPreview[Number(input.dataset.supplierPick)]) {
    supplierPreview[Number(input.dataset.supplierPick)].picked = input.checked;
    const confirm = $('[data-action="confirm-suppliers"]', workspace);
    if (confirm) confirm.disabled = !supplierPreview.some(row => row.picked);
  }
  if (input.id === "excel-file") loadExcel([...input.files]);
  if (!owner && (input.hasAttribute("data-pick-all") || input.dataset.pickMaterial)) {
    const items = visibleItems().filter(item => canFollow(item) &&
      (input.hasAttribute("data-pick-all") || item.id === input.dataset.pickMaterial));
    for (const item of items) {
      if (input.checked) picked.set(item.id, item.version);
      else picked.delete(item.id);
    }
    render();
    const replacement = input.dataset.pickMaterial ? workspace.querySelector(`[data-pick-material="${input.dataset.pickMaterial}"]`) : $("[data-pick-all]", workspace);
    replacement?.focus({preventScroll: true});
  }
  if (input.dataset.boardFilter) {
    const key = {project: "projectFilter", requester: "requesterFilter", supplier: "supplierFilter", from: "fromFilter", to: "toFilter"}[input.dataset.boardFilter];
    restoreBoardFilters({...boardFilters(), [key]: input.value});
    animate(".stacks", 3);
    animate(".detail-body", 4);
  }
});

workspace.addEventListener("toggle", event => {
  const stack = event.target;
  if (!workspace.contains(stack) || !stack.dataset.stack) return;
  if (stack.open) closedStacks.delete(stack.dataset.stack);
  else closedStacks.add(stack.dataset.stack);
}, true);

function fileDrag(event) {
  return data && view !== "suppliers" && !dialog.open && event.dataTransfer?.types.includes("Files");
}

document.addEventListener("dragover", event => {
  if (!fileDrag(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  $("#dropzone", workspace)?.classList.toggle("dragging", Boolean(event.target.closest("#dropzone")));
});
document.addEventListener("dragleave", event => {
  if (event.target.closest("#dropzone") && !event.relatedTarget?.closest?.("#dropzone")) $("#dropzone", workspace)?.classList.remove("dragging");
});
document.addEventListener("drop", event => {
  if (!fileDrag(event)) return;
  event.preventDefault();
  $("#dropzone", workspace)?.classList.remove("dragging");
  loadExcel([...event.dataTransfer.files]);
});

workspace.addEventListener("click", async event => {
  const vendorOption = event.target.closest("[data-supplier-option]");
  if (vendorOption && batch) {
    const entry = data.supplierChoices.find(record => record.id === vendorOption.dataset.supplierOption);
    const container = vendorOption.closest("[data-import-row]");
    if (entry && container) {
      const row = batch.rows[Number(container.dataset.importRow)];
      row.values.preferredSupplier = entry.name;
      row.values.preferredSupplierId = entry.id;
      const input = $('[data-import-key="preferredSupplier"]', container);
      input.value = entry.name;
      input.focus();
      $(".supplier-suggestions", container).hidden = true;
    }
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button) {
    const row = event.target.closest("[data-row-id]");
    if (row) choose(row.dataset.rowId, matchMedia("(max-width: 910px)").matches);
    return;
  }
  const {action, id, notice} = button.dataset;
  if (action === "view") {
    changeView(id);
  } else if (action === "supplier-edit" && !owner) {
    actionForm(action, {id});
  } else if (action === "pick-suppliers" && !owner) {
    $("#supplier-file", workspace).click();
  } else if (action === "supplier-template" && !owner) {
    await downloadSupplierTemplate();
  } else if (action === "confirm-suppliers") {
    await confirmSupplierWorkbook();
  } else if (action === "clear-suppliers") {
    supplierPreview = [];
    supplierMessage = "";
    render();
  } else if (action === "clear-filters") {
    resetBoardFilters();
    syncSelection();
    render();
  } else if (action === "save-filters" || action === "restore-filters") {
    const key = `material-dock-filters:${data.name}`;
    try {
      if (action === "save-filters") {
        localStorage.setItem(key, JSON.stringify(boardFilters()));
        flash("常用筛选已保存在此浏览器。");
      } else {
        const saved = JSON.parse(localStorage.getItem(key));
        if (saved) restoreBoardFilters(saved);
        else flash("此浏览器还没有保存常用筛选。");
      }
    } catch {
      flash("当前浏览器无法保存或读取筛选。");
    }
  } else if (action === "filter") {
    if (id !== filter) {
      filter = id;
      picked.clear();
      syncSelection();
      render();
      animate(".stacks", 3);
      animate(".detail-body", 4);
    }
  } else if (action === "select") {
    choose(id, matchMedia("(max-width: 910px)").matches);
  } else if (action === "notice") {
    resetBoardFilters();
    if (data.items.some(item => item.id === id && ["withdrawn", "void"].includes(item.lifecycle))) filter = "archived";
    choose(id, true);
    try {
      await api("/api/read", {id: Number(notice)});
      await refresh();
    } catch {
      flash("暂时无法标记已读，联网后可重试。");
    }
  } else if (action === "sample") {
    actionForm("create", sample());
  } else if (["create", "receipt", "paste", "identity", "bulk", "revise", "hold", "resume", "withdraw", "void"].includes(action)) {
    dockOpen = false;
    actionForm(action);
  } else if (action === "pick-file") {
    const input = $("#excel-file", workspace);
    input.value = "";
    input.click();
  } else if (action === "apply-defaults") {
    if (!batch?.committing) applyDefaults();
  } else if (action === "commit-import") {
    await commitImport();
  } else if (action === "priority") {
    await togglePriority();
  } else if (action === "export") {
    await exportCurrent();
  } else if (action === "download-attachment") {
    await downloadAttachment(id);
  } else if (action === "password") {
    accountPanel("change", data.name);
  } else if (action === "discard-import" && !batch?.committing) {
    batch = null;
    showImportMessage("");
    render();
  } else if (action === "dock") {
    dockOpen = !dockOpen;
    render();
    if (dockOpen) animate(".dock-menu", 6);
  } else if (action === "logout") {
    try {
      await api("/api/logout", {});
      ++refreshSequence;
      showLogin();
    } catch {
      flash("暂时无法退出，请在连接恢复后再试。");
    }
  }
});

async function resumeSession() {
  const attempt = ++sessionProbe;
  try {
    const account = await api("/api/session");
    if (attempt !== sessionProbe || !account.authenticated) return;
    if (inviteName && inviteName !== account.name) {
      await api("/api/logout", {});
      showLogin();
    } else await refresh();
  } catch (error) {
    if (attempt !== sessionProbe) return;
    if (error.code === "gateway") gatewayBlocked = true;
    if (error.status !== 401 || error.code === "gateway") $("#login-error").textContent = loginError(error);
  }
}

resumeSession();
window.addEventListener("hashchange", () => {
  if (!readInvite()) return;
  if (accountDialog.open && accountAction === "recover") accountPanel("recover", inviteName);
  resumeSession();
});
function refreshWhenSignedIn() {
  if (workspace.hidden || gatewayBlocked || availableRelease || document.hidden) return;
  refresh();
}
setInterval(refreshWhenSignedIn, publicEntry ? 180000 : 3500);
window.addEventListener("online", refreshWhenSignedIn);
document.addEventListener("visibilitychange", refreshWhenSignedIn);
