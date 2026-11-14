"use strict";

const supplierFields = [
  ["name", "供应商名称", 60], ["alias", "简称", 60], ["categories", "主营物料", 160],
  ["contact", "联系人", 60], ["phone", "电话", 80], ["email", "邮箱", 120],
  ["address", "地址", 200], ["note", "备注", 500],
];
let supplierDirectory = [];
let supplierPreview = [];
let supplierMessage = "";
let supplierBusy = false;
let supplierImportWrite = {};

function compactSearch(value) {
  return String(value ?? "").normalize("NFKC").toLowerCase().replace(/[\s\x1c-\x1f\x85]+/gu, "");
}

function fuzzyMatch(query, fields) {
  const haystack = fields.map(compactSearch).join(" ");
  return query.normalize("NFKC").toLowerCase().split(/[\s\x1c-\x1f\x85]+/u).every(token => haystack.includes(token));
}

function supplierPanel() {
  const input = supplierBusy ? "disabled" : "";
  const review = supplierPreview.length ? `<section class="supplier-preview">
    <h2>导入预览 · ${supplierPreview.length} 条</h2>
    <ul>${supplierPreview.map((row, index) => `<li>
      <label><input type="checkbox" data-supplier-pick="${index}" ${row.picked ? "checked" : ""} ${row.issue || supplierBusy ? "disabled" : ""}>
       第 ${row.line} 行 · ${e(row.values.name || "名称空白")}</label>
      <small>${e(row.issue)}</small>
      <details><summary>核对资料</summary><dl>${supplierFields.slice(1).filter(([key]) => row.values[key]).map(([key,label]) =>
        `<div><dt>${label}</dt><dd>${e(row.values[key])}</dd></div>`).join("") || "<div><dd>其他字段为空</dd></div>"}</dl></details>
    </li>`).join("")}</ul>
    <button type="button" class="primary" data-action="confirm-suppliers" ${input || (!supplierPreview.some(row => row.picked) ? "disabled" : "")}>确认所选入库</button>
    <button type="button" class="button" data-action="clear-suppliers" ${input}>关闭预览</button>
  </section>` : "";
  return `<div class="heading"><div><h1>供应商库</h1><p>维护厂家资料，设计师录单时可按名称、简称或主营物料检索。</p></div></div>
    <section class="queue supplier-panel" aria-label="供应商库">
      <div class="supplier-tools"><button class="button" type="button" data-action="supplier-template">下载供应商模板</button>
      <button class="button" type="button" data-action="pick-suppliers" ${input}>上传并预览</button>
      <button class="primary" type="button" data-action="supplier-edit" ${input}>手动新增</button>
      <input type="file" id="supplier-file" hidden accept=".xlsx" aria-label="上传供应商 Excel"></div>
      <p role="status" class="supplier-message">${e(supplierMessage)}</p>${review}
      <div class="supplier-rows">${supplierDirectory.length ? supplierDirectory.map(entry => `<article>
        <div><strong>${e(entry.name)}</strong><small>${e([entry.alias,entry.categories].filter(Boolean).join(" · "))}</small></div>
        <div><span>${e([entry.contact, entry.phone, entry.email].filter(Boolean).join(" · "))}</span><small>${e(entry.address)}</small></div>
        <button class="button" type="button" data-action="supplier-edit" data-id="${e(entry.id)}">编辑</button>
      </article>`).join("") : '<p class="empty">还没有供应商。</p>'}</div>
    </section>`;
}

async function loadSupplierDirectory() {
  const epoch = sessionEpoch;
  try {
    const result = await api("/api/suppliers");
    if (epoch !== sessionEpoch) return;
    supplierDirectory = result.suppliers;
  } catch (error) {
    if (epoch === sessionEpoch) supplierMessage = error.message;
  }
  if (epoch === sessionEpoch && view === "suppliers") render();
}

async function loadSupplierWorkbook(file) {
  if (!file || owner || supplierBusy) return;
  supplierPreview = [];
  supplierImportWrite = {};
  if (!/\.xlsx$/i.test(file.name) || file.size > 5 * 1024 * 1024) {
    supplierMessage = "请选择 5 MB 以内的供应商 .xlsx 模板。";
    render();
    return;
  }
  const epoch = sessionEpoch;
  supplierBusy = true;
  supplierMessage = "正在检查表格…";
  render();
  try {
    const response = await fetch("/api/suppliers/preview", {
      method: "POST", headers: {"Content-Type": workbookType}, body: file, signal: AbortSignal.timeout(30000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "无法读取表格。");
    if (epoch !== sessionEpoch) return;
    supplierPreview = result.rows.map(row => ({...row, picked: !row.issue}));
    const issues = supplierPreview.filter(row => row.issue).length;
    supplierMessage = issues ? `有 ${issues} 条需复核。请修改表格后重新预览，或先入库其他行。` : "核对后可确认入库。";
  } catch (error) {
    if (epoch === sessionEpoch) supplierMessage = error.message;
  } finally {
    if (epoch === sessionEpoch) {
      supplierBusy = false;
      if (view === "suppliers") render();
    }
  }
}

async function confirmSupplierWorkbook() {
  const rows = supplierPreview.filter(row => row.picked && !row.issue).map(row => row.values);
  if (!rows.length || supplierBusy || owner) return;
  const epoch = sessionEpoch;
  supplierBusy = true;
  render();
  try {
    const result = await api("/api/suppliers/confirm", {rows, operationId: writeKey(supplierImportWrite, rows)});
    if (epoch !== sessionEpoch) return;
    supplierPreview = [];
    supplierMessage = `已入库 ${result.count} 家供应商。`;
    await loadSupplierDirectory();
    await refresh();
  } catch (error) {
    if (epoch === sessionEpoch) supplierMessage = error.message;
  } finally {
    if (epoch === sessionEpoch) {
      supplierBusy = false;
      if (view === "suppliers") render();
    }
  }
}

async function downloadSupplierTemplate() {
  try {
    const result = await api("/api/suppliers/template");
    saveFile(result.xlsx, result.filename, workbookType);
  } catch (error) {
    flash(error.message);
  }
}

function preferredSupplierField(seed) {
  return `<div class="wide supplier-chooser">
    ${field("preferredSupplier", "意向供应商（可选）", {value: seed.preferredSupplier, required: false})}
    <input type="hidden" name="preferredSupplierId" value="${e(seed.preferredSupplierId)}">
    <div class="supplier-suggestions" aria-label="供应商匹配结果" hidden></div>
  </div>`;
}

function showSupplierMatches(input) {
  const panel = $(".supplier-suggestions", input.closest(".supplier-chooser"));
  const value = input.value.trim();
  const matches = (data?.supplierChoices || []).filter(entry => fuzzyMatch(value, [entry.name, entry.alias, entry.categories])).slice(0, 8);
  panel.innerHTML = matches.map(entry => `<button type="button" data-supplier-option="${e(entry.id)}">
    <strong>${e(entry.name)}</strong><small>${e([entry.alias, entry.categories].filter(Boolean).join(" · "))}</small></button>`).join("");
  panel.hidden = !matches.length;
}
