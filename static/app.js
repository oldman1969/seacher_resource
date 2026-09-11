/* 全网资源聚合搜索前端逻辑：搜索 / 分组渲染 / 标签 / 实时验证 */

const TYPE_LABEL = {
  video: "视频", book: "书籍", audio: "音频",
  netdisk_share: "网盘分享", magnet: "磁力链接", article: "文章",
};
const PAY_LABEL = {
  free: "免费", paid: "付费", vip: "VIP", unknown: "未知",
};
const PAN_LABEL = {
  baidu: "百度网盘", quark: "夸克", aliyun: "阿里云盘", xunlei: "迅雷云盘",
  "115": "115网盘", uc: "UC网盘", tianyi: "天翼云盘", "123": "123云盘",
  pikpak: "PikPak", magnet: "磁力", ed2k: "ed2k", others: "网盘",
};
const AVAIL_LABEL = {
  available: "✓ 可用", unavailable: "✗ 已失效", unverified: "？验证",
};
const SOURCE_LABEL = { zhihu: "知乎", wechat: "微信" };
const COVER_ICON = { video: "▶", book: "📖", audio: "🎵", netdisk_share: "📁", magnet: "🧲", article: "📄" };

const kwEl = document.getElementById("kw");
const goEl = document.getElementById("go");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const freeOnlyEl = document.getElementById("freeOnly");
const depthEl = document.getElementById("depth");
const intlEl = document.getElementById("intl");
const socialEl = document.getElementById("social");
const panFiltersEl = document.getElementById("panFilters");
const articleFiltersEl = document.getElementById("articleFilters");

const PAGE_SIZE = 50;          // 每页条数
let lastData = null;           // 供「仅看免费」/「网盘过滤」重渲染
let panTypeFilter = null;      // 网盘类型过滤：null=全部，否则为具体 pan_type
let articleSourceFilter = null; // 文章来源过滤：null=全部，zhihu/wechat
let pageState = {};            // { [type]: 当前页码 }

kwEl.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
goEl.addEventListener("click", doSearch);
freeOnlyEl.addEventListener("change", () => render(lastData));

function selectedTypes() {
  return [...document.querySelectorAll(".filters input[data-type]:checked")]
    .map((el) => el.dataset.type);
}

async function doSearch() {
  const q = kwEl.value.trim();
  if (!q) return;
  const types = selectedTypes();
  // 知乎/微信结果类型是 article，勾选时自动纳入
  if (socialEl.checked && !types.includes("article")) types.push("article");
  if (!types.length) { statusEl.textContent = "请至少选择一种资源类型"; return; }

  goEl.disabled = true;
  statusEl.textContent = depthEl.checked ? "深度搜索中（结果上限 2000，可能较慢）…" : "搜索中…";
  resultsEl.innerHTML = '<div class="loading">正在并发查询各数据源…</div>';

  try {
    const url = `/api/search?q=${encodeURIComponent(q)}&types=${types.join(",")}&depth=${depthEl.checked}&include_intl=${intlEl.checked}&include_social=${socialEl.checked}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    lastData = data;
    panTypeFilter = null;   // 重置网盘过滤
    articleSourceFilter = null; // 重置文章来源过滤
    pageState = {};         // 重置分页
    statusEl.innerHTML =
      `耗时 ${data.elapsed_ms} ms` +
      (data.errors.length
        ? ` · <span class="err">${data.errors.map((e) => `${e.source}: ${e.error}`).join("；")}</span>`
        : "");
    render(data);
  } catch (exc) {
    resultsEl.innerHTML = "";
    statusEl.textContent = `搜索失败: ${exc.message}`;
  } finally {
    goEl.disabled = false;
  }
}

function render(data) {
  if (!data) return;
  resultsEl.innerHTML = "";
  const freeOnly = freeOnlyEl.checked;

  // 1. 网盘来源过滤条 + 文章来源过滤条（仅当有相应结果时）
  renderPanFilters(data);
  renderArticleFilters(data);

  let total = 0;

  for (const [type, items] of Object.entries(data.results)) {
    let shown = freeOnly ? items.filter((r) => r.payment === "free") : items;
    // 网盘类型过滤（只作用于 netdisk_share）
    if (type === "netdisk_share" && panTypeFilter) {
      shown = shown.filter((r) => r.pan_type === panTypeFilter);
    }
    // 文章来源过滤（只作用于 article）
    if (type === "article" && articleSourceFilter) {
      shown = shown.filter((r) => r.source === articleSourceFilter);
    }
    if (!shown.length) continue;
    total += shown.length;

    const group = document.createElement("div");
    group.className = "group";
    group.innerHTML = `<h2>${TYPE_LABEL[type] || type}<span class="count">${shown.length} 条</span></h2>`;

    // 组内分页
    const totalPages = Math.ceil(shown.length / PAGE_SIZE);
    const page = Math.min(pageState[type] || 0, totalPages - 1);
    const pageItems = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

    const cards = document.createElement("div");
    cards.className = "cards";
    pageItems.forEach((r) => cards.appendChild(renderCard(r)));
    group.appendChild(cards);

    if (totalPages > 1) group.appendChild(makePager(type, page, totalPages));
    resultsEl.appendChild(group);
  }

  if (!total) {
    resultsEl.innerHTML = '<div class="empty">没有找到相关资源，换个关键词试试？</div>';
  }
}

function renderPanFilters(data) {
  panFiltersEl.innerHTML = "";
  const netdisk = data.results.netdisk_share || [];
  const panTypes = [...new Set(netdisk.map((r) => r.pan_type).filter(Boolean))];
  if (!panTypes.length) return;

  const wrap = document.createElement("div");
  wrap.className = "pan-filter-label";
  wrap.textContent = "网盘来源：";

  const all = document.createElement("button");
  all.className = "pan-chip" + (panTypeFilter === null ? " active" : "");
  all.textContent = "全部";
  all.onclick = () => { panTypeFilter = null; render(lastData); };
  wrap.appendChild(all);

  for (const t of panTypes) {
    const chip = document.createElement("button");
    chip.className = "pan-chip" + (panTypeFilter === t ? " active" : "");
    chip.textContent = PAN_LABEL[t] || t;
    chip.onclick = () => { panTypeFilter = t; render(lastData); };
    wrap.appendChild(chip);
  }
  panFiltersEl.appendChild(wrap);
}

function renderArticleFilters(data) {
  articleFiltersEl.innerHTML = "";
  const articles = data.results.article || [];
  const sources = [...new Set(articles.map((r) => r.source).filter(Boolean))];
  if (!sources.length) return;

  const wrap = document.createElement("div");
  wrap.className = "pan-filter-label";
  wrap.textContent = "文章来源：";

  const all = document.createElement("button");
  all.className = "pan-chip" + (articleSourceFilter === null ? " active" : "");
  all.textContent = "全部";
  all.onclick = () => { articleSourceFilter = null; render(lastData); };
  wrap.appendChild(all);

  for (const s of sources) {
    const chip = document.createElement("button");
    chip.className = "pan-chip" + (articleSourceFilter === s ? " active" : "");
    chip.textContent = SOURCE_LABEL[s] || s;
    chip.onclick = () => { articleSourceFilter = s; render(lastData); };
    wrap.appendChild(chip);
  }
  articleFiltersEl.appendChild(wrap);
}

function makePager(type, page, totalPages) {
  const pager = document.createElement("div");
  pager.className = "pager";

  const prev = document.createElement("button");
  prev.textContent = "‹ 上一页";
  prev.disabled = page === 0;
  prev.onclick = () => { pageState[type] = page - 1; render(lastData); };

  const info = document.createElement("span");
  info.className = "pager-info";
  info.textContent = `${page + 1} / ${totalPages}`;

  const next = document.createElement("button");
  next.textContent = "下一页 ›";
  next.disabled = page >= totalPages - 1;
  next.onclick = () => { pageState[type] = page + 1; render(lastData); };

  pager.append(prev, info, next);
  return pager;
}

function renderCard(r) {
  const card = document.createElement("div");
  card.className = "card";

  // 封面
  if (r.cover) {
    const img = document.createElement("img");
    img.className = "cover";
    img.src = r.cover;
    img.loading = "lazy";
    img.onerror = () => img.replaceWith(placeholder(r));
    card.appendChild(img);
  } else {
    card.appendChild(placeholder(r));
  }

  const body = document.createElement("div");
  body.className = "body";

  // 标题
  const title = document.createElement("div");
  title.className = "title";
  const a = document.createElement("a");
  a.href = r.url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = r.title;
  title.appendChild(a);
  body.appendChild(title);

  // 元信息
  const meta = document.createElement("div");
  meta.className = "meta";
  const bits = [];
  if (r.author) bits.push(r.author);
  if (r.publish_date) bits.push(r.publish_date);
  if (r.extra?.duration) bits.push(r.extra.duration);
  if (r.extra?.size) bits.push(r.extra.size);
  if (r.extra?.album) bits.push(`《${r.extra.album}》`);
  if (r.extra?.play_count != null) bits.push(`${fmtCount(r.extra.play_count)} 播放`);
  meta.textContent = bits.join(" · ");
  body.appendChild(meta);

  // 标签行
  const tags = document.createElement("div");
  tags.className = "tags";

  const pay = document.createElement("span");
  pay.className = `tag pay-${r.payment}`;
  pay.textContent = PAY_LABEL[r.payment] + (r.payment_note ? `·${r.payment_note}` : "");
  tags.appendChild(pay);

  const src = document.createElement("span");
  src.className = "tag src";
  src.textContent = r.source;
  tags.appendChild(src);

  if (r.pan_type) {
    const pan = document.createElement("span");
    pan.className = "tag pan";
    pan.textContent = PAN_LABEL[r.pan_type] || r.pan_type;
    tags.appendChild(pan);
  }

  // 提取码
  if (r.password) {
    const pwd = document.createElement("span");
    pwd.className = "pwd-chip";
    pwd.textContent = `提取码 ${r.password} 📋`;
    pwd.title = "点击复制";
    pwd.onclick = () => {
      navigator.clipboard?.writeText(r.password);
      pwd.textContent = `提取码 ${r.password} ✓`;
    };
    tags.appendChild(pwd);
  }

  // 磁力链接：复制按钮（magnet: 协议浏览器无法直接打开）
  if (r.type === "magnet") {
    const copy = document.createElement("span");
    copy.className = "pwd-chip";
    copy.textContent = "复制磁力链接 📋";
    copy.title = "点击复制 magnet 链接，粘贴到迅雷/qBittorrent 等下载工具";
    copy.onclick = () => {
      navigator.clipboard?.writeText(r.url);
      copy.textContent = "已复制 ✓";
      setTimeout(() => { copy.textContent = "复制磁力链接 📋"; }, 1500);
    };
    tags.appendChild(copy);
  }

  // 可用性（未验证可点击实时探测）
  const avail = document.createElement("span");
  avail.className = `avail avail-${r.availability}`;
  avail.textContent = AVAIL_LABEL[r.availability];
  if (r.availability === "unverified") {
    avail.title = "点击实时验证链接";
    avail.onclick = () => verifyResource(r, avail);
  }
  tags.appendChild(avail);

  body.appendChild(tags);
  card.appendChild(body);
  return card;
}

function placeholder(r) {
  const d = document.createElement("div");
  d.className = "cover-placeholder";
  d.textContent = COVER_ICON[r.type] || "📄";
  return d;
}

async function verifyResource(r, el) {
  el.textContent = "验证中…";
  el.style.color = "#2563eb";
  try {
    const resp = await fetch("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resource_id: r.resource_id, url: r.url, source: r.source,
        pan_type: r.pan_type, type: r.type,
      }),
    });
    const data = await resp.json();
    r.availability = data.availability;
    el.className = `avail avail-${data.availability}`;
    el.textContent = AVAIL_LABEL[data.availability];
    el.onclick = null;
    if (data.detail) el.title = data.detail;
  } catch (exc) {
    el.textContent = "验证失败";
    el.title = exc.message;
  }
}

// ------------------------------------------------------------ 设置弹窗（右上角齿轮）

const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const lockedStep = document.getElementById("lockedStep");
const authStep = document.getElementById("authStep");
const configStep = document.getElementById("configStep");
const adminPwdEl = document.getElementById("adminPwd");
const authGoEl = document.getElementById("authGo");
const authErrEl = document.getElementById("authErr");
const zhihuStatusEl = document.getElementById("zhihuStatus");
const zhihuCookieEl = document.getElementById("zhihuCookie");
const saveZhihuEl = document.getElementById("saveZhihu");
const zhihuSaveMsgEl = document.getElementById("zhihuSaveMsg");

function showStep(step) {
  [lockedStep, authStep, configStep].forEach((s) => s.classList.add("hidden"));
  step.classList.remove("hidden");
}

function openModal() {
  settingsModal.classList.remove("hidden");
  // 先试已有 admin 会话（2 小时内免重复验证）
  loadZhihuStatus().then((state) => {
    if (state === "ok") {
      showStep(configStep);
    } else if (state === "auth") {
      showStep(authStep);
      adminPwdEl.value = "";
      adminPwdEl.focus();
    } else {
      showStep(lockedStep);  // 管理未启用
    }
  });
}

function closeModal() {
  settingsModal.classList.add("hidden");
  authErrEl.textContent = "";
  zhihuSaveMsgEl.textContent = "";
}

settingsBtn.addEventListener("click", openModal);
settingsModal.querySelector(".modal-mask").addEventListener("click", closeModal);
settingsModal.querySelector(".modal-close").addEventListener("click", closeModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsModal.classList.contains("hidden")) closeModal();
});
adminPwdEl.addEventListener("keydown", (e) => { if (e.key === "Enter") authGoEl.click(); });

authGoEl.addEventListener("click", async () => {
  authGoEl.disabled = true;
  authErrEl.textContent = "";
  try {
    const r = await fetch("/api/admin/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: adminPwdEl.value }),
    });
    if (r.status === 403) { showStep(lockedStep); return; }
    if (!r.ok) throw new Error(r.status === 401 ? "密码错误" : `HTTP ${r.status}`);
    // 验证通过 → 切到配置界面
    showStep(configStep);
    await loadZhihuStatus();
  } catch (exc) {
    authErrEl.textContent = exc.message;
  } finally {
    authGoEl.disabled = false;
  }
});

async function loadZhihuStatus() {
  try {
    const r = await fetch("/api/admin/zhihu-cookie/status");
    if (r.status === 401) return "auth";     // 需验证密码
    if (r.status === 403) return "locked";   // 管理未启用
    if (!r.ok) throw new Error();
    const d = await r.json();
    zhihuStatusEl.textContent = d.configured
      ? "状态：✓ 已配置（失效时重新粘贴保存即可，无需重启）"
      : "状态：✗ 未配置（知乎源不可用，不影响其他源）";
    zhihuStatusEl.classList.toggle("ok", d.configured);
    return "ok";
  } catch {
    zhihuStatusEl.textContent = "状态：无法获取";
    return "ok";  // 已过验证，只是状态获取失败
  }
}

saveZhihuEl.addEventListener("click", async () => {
  const cookie = zhihuCookieEl.value.trim();
  if (!cookie) { zhihuSaveMsgEl.textContent = "请先粘贴 cookie"; return; }
  saveZhihuEl.disabled = true;
  zhihuSaveMsgEl.textContent = "保存中…";
  try {
    const r = await fetch("/api/admin/zhihu-cookie", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookie }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    zhihuSaveMsgEl.textContent = "✓ 已保存并生效";
    zhihuCookieEl.value = "";
    loadZhihuStatus();
  } catch (exc) {
    zhihuSaveMsgEl.textContent = `保存失败: ${exc.message}`;
  } finally {
    saveZhihuEl.disabled = false;
  }
});

function fmtCount(n) {
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
  return String(n);
}
