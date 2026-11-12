/* ============================================================
   battery技术 — 前端逻辑（原生 JS，无依赖）
   ============================================================ */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const esc = (s) =>
    String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  const pad = (n) => String(n).padStart(2, "0");

  function nowLocal() {
    const d = new Date();
    return (
      d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes())
    );
  }

  function parseDate(iso) {
    if (!iso) return null;
    const d = new Date(String(iso).replace(" ", "T"));
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtTime(iso) {
    const d = parseDate(iso);
    if (!d) return "—";
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return (
      (sameYear ? "" : d.getFullYear() + "-") +
      pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes())
    );
  }

  function fmtDay(iso) {
    const d = parseDate(iso);
    if (!d) return "—";
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function durText(startIso, endIso) {
    const a = parseDate(startIso);
    const b = endIso ? parseDate(endIso) : new Date();
    if (!a || !b) return "—";
    let s = Math.max(0, (b.getTime() - a.getTime()) / 1000);
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    if (d > 0) return d + " 天 " + h + " 小时";
    if (h > 0) return h + " 小时 " + m + " 分";
    return m + " 分钟";
  }

  function statusClass(s) {
    if (!s) return "none";
    if (/异常|故障|报警/.test(s)) return "bad";
    if (/停机|停/.test(s)) return "idle";
    if (/静置|搁置|暂停/.test(s)) return "blue";
    if (/待检|检测|待/.test(s)) return "warn";
    if (/运行|充电|放电|循环/.test(s)) return "ok";
    return "blue";
  }

  function fmtVal(v) {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
    return String(v);
  }

  /* ---------------------------------------------------------- 状态 */

  const S = {
    data: null,
    tab: "board",
    boardFilter: "all",
    boardQuery: "",
    density: "normal",
    reportChannelId: null,
    batteryQuery: "",
    batteryList: [],
    activeBatteryId: null,
    cmpItems: [],
    cmpSnaps: [],
    testMap: {},
  };

  /* 卡片密度：记住上次选择；第一次打开时手机默认紧凑 */
  function initDensity() {
    let saved = null;
    try { saved = localStorage.getItem("bt_density"); } catch (e) { saved = null; }
    if (saved !== "dense" && saved !== "normal") {
      saved = window.innerWidth <= 900 ? "dense" : "normal";
    }
    S.density = saved;
  }

  function setDensity(d) {
    S.density = d === "dense" ? "dense" : "normal";
    try { localStorage.setItem("bt_density", S.density); } catch (e) { /* 隐私模式 */ }
    syncDensitySeg();
    renderBoard();
  }

  function syncDensitySeg() {
    const seg = $("#densitySeg");
    if (!seg) return;
    $$("button", seg).forEach((b) =>
      b.classList.toggle("active", b.dataset.d === S.density)
    );
  }

  /* ---------------------------------------------------------- 网络 */

  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.method || "GET", headers: {}, cache: "no-store" };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, init);
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) throw new Error(data.error || "请求失败 (" + res.status + ")");
    return data;
  }

  let toastTimer = null;
  function toast(msg, kind) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast"; }, 2600);
  }

  /* ---------------------------------------------------------- 弹窗 */

  function openModal(opt) {
    $("#modalTitle").textContent = opt.title || "";
    $("#modalBody").innerHTML = opt.body || "";
    $("#modalFoot").innerHTML = opt.foot || "";
    $(".modal").classList.toggle("wide", !!opt.wide);
    $("#modal").classList.remove("hidden");
    if (opt.onOpen) opt.onOpen();
  }

  function closeModal() { $("#modal").classList.add("hidden"); }

  function channelById(id) {
    return (S.data ? S.data.channels : []).find((c) => c.id === id) || null;
  }

  /* ============================================================
     全局加载
     ============================================================ */

  async function loadState() {
    const data = await api("/api/state");
    S.data = data;
    document.title = data.settings.app_name || "battery技术";
    $("#appName").textContent = data.settings.app_name || "battery技术";
    $("#dbPath").textContent = "data/battery.db";
    renderStats();
    renderBoard();
    renderReportChannels();
    renderSettings();
    renderCompareFilters();
    if (S.tab === "compare") loadCompare();
    if (S.tab === "battery") loadBatteries(S.batteryQuery);
    if (S.tab === "report") loadRecent();
  }

  function renderStats() {
    const st = S.data ? S.data.stats : { channels: 0, occupied: 0, batteries: 0, snapshots: 0 };
    $("#stats").innerHTML =
      '<span class="stat"><b>' + st.channels + '</b> 通道</span>' +
      '<span class="stat"><b>' + st.occupied + '</b> 在装</span>' +
      '<span class="stat"><b>' + st.batteries + '</b> 电池</span>' +
      '<span class="stat"><b>' + st.snapshots + '</b> 报送记录</span>';
  }

  /* ============================================================
     通道看板
     ============================================================ */

  function renderBoard() {
    if (!S.data) return;
    const grid = $("#boardGrid");
    const empty = $("#boardEmpty");
    grid.classList.toggle("dense", S.density === "dense");
    syncDensitySeg();
    let list = S.data.channels.slice();
    const q = S.boardQuery.trim().toLowerCase();

    if (q) {
      list = list.filter((c) => {
        const bat = c.current && c.current.battery ? c.current.battery.code.toLowerCase() : "";
        return (
          c.code.toLowerCase().includes(q) ||
          (c.name || "").toLowerCase().includes(q) ||
          bat.includes(q)
        );
      });
    }
    if (S.boardFilter === "busy") list = list.filter((c) => c.current);
    if (S.boardFilter === "empty") list = list.filter((c) => !c.current);

    // 只有「既没有通道、也没有分组」时才显示整页空状态。
    // 有分组时必须继续往下渲染，否则刚建的空分组会看不见。
    if (S.data.channels.length === 0 && (S.data.groups || []).length === 0) {
      grid.innerHTML = "";
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");

    const filtering = !!q || S.boardFilter !== "all";
    S.canDrag = !filtering;
    $("#dragHint").textContent = filtering ? "筛选状态下不能拖动排序" : "可拖动排序 · 拖到别的组即改组";

    const groups = S.data.groups || [];
    // 有分组时，即使一个通道都没有也要把分组画出来（刚建的空分组要能看到）
    if (list.length === 0 && (filtering || groups.length === 0)) {
      grid.innerHTML = '<div class="empty"><p>没有匹配的通道</p></div>';
      return;
    }

    const byGroup = new Map();
    list.forEach((c) => {
      const gid = c.group_id || 0;
      if (!byGroup.has(gid)) byGroup.set(gid, []);
      byGroup.get(gid).push(c);
    });

    // 还没有任何分组时，就用一个不带头部的普通网格
    if (groups.length === 0) {
      grid.innerHTML = bodyHtml(0, byGroup.get(0) || list, true);
      return;
    }

    let html = "";
    groups.forEach((g) => {
      const items = byGroup.get(g.id) || [];
      if (filtering && items.length === 0) return;
      html += sectionHtml(g, items, filtering);
    });
    const loose = byGroup.get(0) || [];
    if (loose.length || !filtering) {
      html += sectionHtml({ id: 0, name: "未分组" }, loose, filtering, true);
    }
    grid.innerHTML = html;
    bindCollapse(grid, "bt.collapsedGroups", ".group", ".group-head");
  }

  function sectionHtml(g, items, filtering, isUngrouped) {
    const gid = g.id || 0;
    const draggableHead = S.canDrag && !filtering ? ' draggable="true"' : "";
    const head = isUngrouped
      ? '<header class="group-head"' + draggableHead + '><span class="group-grip">⋮⋮</span>' +
        '<span class="group-caret"></span><span class="group-name plain">未分组</span>' +
        '<span class="badge">' + items.length + " 个通道</span></header>"
      : '<header class="group-head"' + draggableHead + '><span class="group-grip">⋮⋮</span>' +
        '<span class="group-caret"></span>' +
          '<span class="group-name" data-edit="group" data-gid="' + gid + '" title="点击重命名分组">' +
            esc(g.name) + "</span>" +
          '<span class="badge">' + items.length + " 个通道</span>" +
          '<button class="icon-btn" data-act="delGroup" data-gid="' + gid + '" title="删除分组">✕</button>' +
        "</header>";
    return (
      '<section class="group' + (isCollapsed("bt.collapsedGroups", gid) ? " collapsed" : "") +
      '" data-gid="' + gid + '" data-group-id="' + gid + '">' + head +
        bodyHtml(gid, items, false, filtering) +
      "</section>"
    );
  }

  function bodyHtml(gid, items, plain, filtering) {
    const cells = items.length
      ? items.map(cardHtml).join("")
      : (plain ? "" : '<div class="group-empty">把通道拖到这里</div>');
    const cls = "grid group-body" + (plain ? " plain" : "");
    return '<div class="' + cls + '" data-group-id="' + gid + '">' + cells + "</div>";
  }

  function codeSpan(c) {
    return '<span class="ch-code" data-edit="code" title="点击修改通道编号">' + esc(c.code) + "</span>";
  }

  function nameSpan(c) {
    return (
      '<span class="ch-name' + (c.name ? "" : " is-empty") + '" data-edit="name" title="点击修改名称">' +
      (c.name ? esc(c.name) : "＋ 名称") +
      "</span>"
    );
  }

  function delBtn() {
    return '<button class="card-del" data-act="delChannel" title="删除这个通道">✕</button>';
  }

  function testMark(cid, compact) {
    const t = (S.testMap || {})[cid];
    if (!t) return "";
    const label = compact ? "📈" : "📈 " + t.cycles + " 圈";
    return '<button class="ts-mark' + (compact ? " mini" : "") + '" data-act="openTest" data-tid="' +
      t.id + '" title="查看充放电曲线（' + t.cycles + ' 圈）">' + label + "</button>";
  }

  function cardHtml(c) {
    return S.density === "dense" ? cardHtmlDense(c) : cardHtmlNormal(c);
  }

  /* 紧凑卡片：只留「通道号 / 电池 / 状态 / 最新数值」，一屏能看十几个 */
  function cardHtmlDense(c) {
    const cur = c.current;
    const last = c.last;
    const st = last ? last.status : "";
    const cls = statusClass(st);
    const b = cur ? (cur.battery || {}) : null;
    const dragAttr = ' data-group="' + (c.group_id || 0) + '"';
    const metrics = (last && last.values ? last.values.slice(0, 3) : [])
      .map((v) => esc(fmtVal(v.value)) + (v.unit ? esc(v.unit) : ""))
      .join(" · ");

    return (
      '<div class="card dense ' + (cur ? "busy st-" + cls : "empty-channel") + '"' +
        ' data-channel="' + c.id + '"' + dragAttr + ">" +
        '<div class="dense-top">' +
          '<span class="ch-code">' + esc(c.code) + "</span>" +
          (cur ? '<span class="dense-status ' + cls + '">' + esc(st || "未报送") + "</span>" : "") +
          testMark(c.id, true) +
          '<button class="dense-up" data-act="uploadNdax" title="上传 .ndax 数据">📤</button>' +
          '<button class="card-del" data-act="delChannel" title="删除这个通道">✕</button>' +
        "</div>" +
        '<div class="dense-bat">' + (b ? esc(b.code) : "空通道") + "</div>" +
        (metrics ? '<div class="dense-metrics">' + metrics + "</div>" : "") +
      "</div>"
    );
  }

  function cardHtmlNormal(c) {
    const cur = c.current;
    const last = c.last;
    const st = last ? last.status : "";
    const cls = statusClass(st);
    const drag = ' data-group="' + (c.group_id || 0) + '"';

    if (!cur) {
      return (
        '<div class="card empty-channel" data-channel="' + c.id + '"' + drag + ">" +
          '<div class="card-head">' +
            codeSpan(c) +
            '<span class="chip none">空通道</span>' +
            nameSpan(c) +
            delBtn() +
          "</div>" +
          '<div class="no-battery">暂无电池</div>' +
          '<div class="card-foot">' +
            "<span>从未装机</span>" +
            testMark(c.id) +
            '<div class="card-actions">' +
              '<button class="mini-btn accent" data-act="swap">装电池</button>' +
            "</div>" +
          "</div>" +
        "</div>"
      );
    }

    const b = cur.battery || {};
    const meta = [b.batch ? "批次 " + b.batch : "", b.model || "", b.rated_capacity ? "额定 " + b.rated_capacity + "Ah" : ""]
      .filter(Boolean).join(" · ");

    let metrics = "";
    if (last && last.values && last.values.length) {
      metrics = '<div class="metrics">' + last.values.slice(0, 4).map((v) =>
        '<span class="metric">' + esc(v.label) + " <b>" + esc(fmtVal(v.value)) + "</b>" +
        (v.unit ? '<span class="u">' + esc(v.unit) + "</span>" : "") + "</span>"
      ).join("") + "</div>";
    }

    return (
      '<div class="card busy st-' + cls + '" data-channel="' + c.id + '"' + drag + ">" +
        '<div class="card-head">' +
          codeSpan(c) +
          '<span class="chip ' + cls + '">' + esc(st || "未报送") + "</span>" +
          nameSpan(c) +
          delBtn() +
        "</div>" +
        '<div class="bat-block">' +
          '<div class="bat-code">' + esc(b.code || "?") + "</div>" +
          (meta ? '<div class="bat-meta">' + esc(meta) + "</div>" : "") +
        "</div>" +
        metrics +
        (last ? '<div class="hint">最近报送 ' + esc(fmtTime(last.taken_at)) + "</div>" : "") +
        '<div class="card-foot">' +
          "<span>已装 " + esc(durText(cur.started_at)) + "</span>" +
          testMark(c.id) +
          '<div class="card-actions">' +
            '<button class="mini-btn" data-act="report">报送</button>' +
            '<button class="mini-btn accent" data-act="swap">换电池</button>' +
            '<button class="mini-btn" data-act="uploadNdax" title="上传新威 .ndax 数据">📤</button>' +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  /* ----------------------------------------------------------
     看板上的就地改名
     ---------------------------------------------------------- */

  function startInlineEdit(el, channelId, field) {
    if (el.dataset.editing === "1") return;
    const ch = channelById(channelId);
    if (!ch) return;

    const original = (field === "code" ? ch.code : ch.name) || "";
    const before = el.textContent;

    el.dataset.editing = "1";
    el.classList.add("editing");
    el.innerHTML = "";
    const input = document.createElement("input");
    input.className = "inline-edit" + (field === "code" ? " code" : "");
    input.value = original;
    input.placeholder = field === "code" ? "通道编号" : "设备名 / 位置";
    input.autocomplete = "off";
    input.spellcheck = false;
    el.appendChild(input);
    input.focus();
    input.select();

    let settled = false;

    const finish = (text) => {
      settled = true;
      delete el.dataset.editing;
      el.classList.remove("editing");
      el.textContent = text;
    };

    const cancel = () => {
      if (settled) return;
      finish(before);
      if (field === "name" && !original) el.classList.add("is-empty");
    };

    const commit = async () => {
      if (settled) return;
      const val = input.value.trim();
      if (field === "code" && !val) {
        toast("通道编号不能为空", "err");
        input.focus();
        return;
      }
      if (val === original.trim()) { cancel(); return; }
      settled = true;
      delete el.dataset.editing;
      try {
        const body = field === "code" ? { code: val } : { name: val };
        await api("/api/channels/" + channelId, { method: "PUT", body: body });
        toast(field === "code" ? "通道编号已改为 " + val : (val ? "名称已更新" : "名称已清空"), "ok");
      } catch (e) {
        toast(e.message, "err");
      }
      await loadState();
    };

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        commit();
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        cancel();
      }
    });
    input.addEventListener("blur", () => { if (!settled) commit(); });
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("mousedown", (e) => e.stopPropagation());
  }

  /* ----------------------------------------------------------
     分组：新建 / 改名 / 删除
     ---------------------------------------------------------- */

  function openNewGroup() {
    openModal({
      title: "新建分组",
      body:
        '<div class="form-row"><label>分组名称</label>' +
        '<input id="ngName" class="input" placeholder="例如：柜A / 一层台架 / 化成间" autocomplete="off"></div>' +
        '<p class="hint">分组只是用来归拢通道，方便看板上一眼找到。建好后把通道拖进去即可。</p>',
      foot: '<div class="spacer"></div><button class="btn" id="ngCancel">取消</button>' +
            '<button class="btn primary" id="ngOk">创建</button>',
      onOpen: () => {
        const el = $("#ngName");
        el.focus();
        const submit = async () => {
          const name = el.value.trim();
          if (!name) { toast("请填写分组名称", "err"); el.focus(); return; }
          try {
            await api("/api/groups", { method: "POST", body: { name: name } });
            closeModal();
            toast("分组「" + name + "」已创建", "ok");
            await loadState();
          } catch (e) { toast(e.message, "err"); }
        };
        $("#ngOk").onclick = submit;
        el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
        $("#ngCancel").onclick = closeModal;
      },
    });
  }

  /* ----------------------------------------------------------
     添加通道（看板直接加，不用去设置页）
     ---------------------------------------------------------- */

  function suggestNextRow() {
    let maxRow = 0;
    const all = (S.data.channels || []).concat(S.data.archived_channels || []);
    all.forEach((c) => {
      const m = /^(\d+)-\d+$/.exec(c.code || "");
      if (m) maxRow = Math.max(maxRow, Number(m[1]));
    });
    return maxRow + 1;
  }

  function openAddChannels() {
    const groups = S.data.groups || [];
    const groupSelect = groups.length
      ? '<div class="form-row"><label>放入分组</label><select id="acGroup" class="input">' +
        '<option value="0">未分组</option>' +
        groups.map((g) => '<option value="' + g.id + '">' + esc(g.name) + "</option>").join("") +
        "</select></div>"
      : "";
    const startRow = suggestNextRow();

    openModal({
      title: "添加通道",
      body:
        '<div class="ac-row">' +
          '<div class="form-row"><label>第几排</label>' +
          '<input id="acRow" class="input" type="number" min="1" max="99" step="1" value="' + startRow + '" autocomplete="off"></div>' +
          '<div class="form-row"><label>本排通道数</label>' +
          '<input id="acCount" class="input" type="number" min="1" max="99" step="1" value="8" autocomplete="off"></div>' +
        "</div>" +
        groupSelect +
        '<div class="ac-preview">' +
          '<div class="ac-preview-head">将创建 <b id="acTotal">8</b> 个通道 ' +
          '<span class="hint">· 编号可以直接点进去改</span></div>' +
          '<div id="acCodes" class="code-grid"></div>' +
        "</div>" +
        '<div id="acAdded" class="chip-row"></div>',
      foot:
        '<div class="spacer"></div>' +
        '<button class="btn" id="acClose">取消</button>' +
        '<button class="btn primary" id="acOk">添加</button>',
      onOpen: () => {
        const added = [];

        const existingCodes = () => {
          // 只在当前分组内查重：不同分组可以共用 2-1
          const gid = Number(($("#acGroup") || {}).value || 0);
          return new Set(
            (S.data.channels || []).concat(S.data.archived_channels || [])
              .filter((c) => (Number(c.group_id) || 0) === gid)
              .map((c) => c.code)
          );
        };

        const validate = () => {
          const known = existingCodes();
          const seen = new Set();
          let ok = true;
          $$("#acCodes .code-input").forEach((inp) => {
            const v = inp.value.trim();
            let bad = false;
            if (!v) bad = true;
            else if (known.has(v)) bad = true;
            else if (seen.has(v)) bad = true;
            seen.add(v);
            inp.classList.toggle("bad", bad);
            inp.title = bad ? "这个编号为空、或本组内已存在" : "";
            if (bad) ok = false;
          });
          return ok;
        };

        const buildCodes = () => {
          const r = Math.max(1, parseInt($("#acRow").value, 10) || 1);
          let n = parseInt($("#acCount").value, 10);
          if (isNaN(n) || n < 1) n = 0;
          n = Math.min(n, 99);
          const box = $("#acCodes");
          box.innerHTML = "";
          for (let i = 1; i <= n; i++) {
            const inp = document.createElement("input");
            inp.className = "code-input";
            inp.value = r + "-" + i;
            inp.autocomplete = "off";
            inp.spellcheck = false;
            box.appendChild(inp);
          }
          $("#acTotal").textContent = n;
          validate();
        };

        const renderAdded = () => {
          $("#acAdded").innerHTML = added.length
            ? '<span class="hint" style="width:100%">本次已添加 ' + added.length + " 个：</span>" +
              added.map((c) => '<span class="chip ok">' + esc(c) + "</span>").join("")
            : "";
        };

        const submit = async () => {
          const inputs = $$("#acCodes .code-input");
          if (!inputs.length) { toast("请填写本排通道数", "err"); $("#acCount").focus(); return; }
          if (!validate()) {
            toast("有编号为空、或本组内已存在，先改一下标红的", "err");
            return;
          }
          const codes = inputs.map((i) => i.value.trim());
          const gidEl = $("#acGroup");
          const gid = gidEl ? Number(gidEl.value) || null : null;
          const btn = $("#acOk");
          btn.disabled = true;
          try {
            const r = await api("/api/channels/create-batch", {
              method: "POST",
              body: { codes: codes, group_id: gid },
            });
            added.push.apply(added, r.created);
            renderAdded();
            await loadState();
            // 加完自动跳到下一排，方便连续录入
            $("#acRow").value = suggestNextRow();
            buildCodes();
            toast("已添加 " + r.created.length + " 个通道", "ok");
          } catch (e) {
            toast(e.message, "err");
          } finally {
            btn.disabled = false;
          }
        };

        $("#acOk").onclick = submit;
        $("#acClose").onclick = closeModal;
        $("#acRow").addEventListener("input", buildCodes);
        $("#acCount").addEventListener("input", buildCodes);
        $("#acCodes").addEventListener("input", validate);
        $("#acCount").addEventListener("keydown", (e) => {
          if (e.key === "Enter") { e.preventDefault(); submit(); }
        });

        buildCodes();
        $("#acRow").focus();
        $("#acRow").select();
      },
    });
  }

  function startGroupRename(el, gid) {
    if (el.dataset.editing === "1") return;
    const g = (S.data.groups || []).find((x) => x.id === gid);
    if (!g) return;
    const original = g.name;
    const before = el.textContent;

    el.dataset.editing = "1";
    el.classList.add("editing");
    el.innerHTML = "";
    const input = document.createElement("input");
    input.className = "inline-edit";
    input.value = original;
    input.placeholder = "分组名称";
    input.autocomplete = "off";
    input.spellcheck = false;
    el.appendChild(input);
    input.focus();
    input.select();

    let settled = false;
    const finish = (text) => {
      settled = true;
      delete el.dataset.editing;
      el.classList.remove("editing");
      el.textContent = text;
    };
    const cancel = () => { if (!settled) finish(before); };
    const commit = async () => {
      if (settled) return;
      const val = input.value.trim();
      if (!val) { toast("分组名称不能为空", "err"); input.focus(); return; }
      if (val === original) { cancel(); return; }
      settled = true;
      try {
        await api("/api/groups/" + gid, { method: "PUT", body: { name: val } });
        toast("分组已改名", "ok");
      } catch (e) { toast(e.message, "err"); }
      await loadState();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(); }
    });
    input.addEventListener("blur", () => { if (!settled) commit(); });
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("mousedown", (e) => e.stopPropagation());
  }

  async function deleteGroup(gid) {
    const g = (S.data.groups || []).find((x) => x.id === gid);
    if (!g) return;
    const items = (S.data.channels || []).filter((c) => c.group_id === gid);
    const n = items.length;
    let msg = "删除分组「" + g.name + "」？";
    if (n) {
      const withHistory = items.filter((c) => {
        const ct = c.counts || {};
        return (ct.snapshots || 0) + (ct.placements || 0) > 0;
      }).length;
      const occupied = items.filter((c) => c.current).length;
      msg += "\n\n组里的 " + n + " 个通道会一起删除：";
      if (withHistory) msg += "\n· " + withHistory + " 个有记录 → 归档隐藏（数据保留，可在「设置 → 已归档通道」恢复）";
      if (n - withHistory) msg += "\n· " + (n - withHistory) + " 个没有记录 → 彻底删除";
      if (occupied) msg += "\n· 其中 " + occupied + " 个还装着电池，会先自动下机";
      msg += "\n\n删除后这些通道就从看板消失了。";
    }
    if (!confirm(msg)) return;
    try {
      const r = await api("/api/groups/" + gid, { method: "DELETE" });
      toast(r.message || "已删除", "ok");
      await loadState();
    } catch (e) { toast(e.message, "err"); }
  }

  async function deleteChannel(cid) {
    const ch = channelById(cid);
    if (!ch) return;
    const snaps = (ch.counts && ch.counts.snapshots) || 0;
    const places = (ch.counts && ch.counts.placements) || 0;
    const lines = ["删除通道「" + ch.code + "」？", ""];
    if (ch.current) {
      lines.push("⚠ 上面还装着 " + ch.current.battery.code + "，删除时会自动下机。");
    }
    if (snaps || places) {
      lines.push("它有 " + places + " 条装机、 " + snaps + " 条报送记录。");
      lines.push("删除后会从看板隐藏（归档），历史数据保留，可在「设置 → 通道管理」里恢复。");
    } else {
      lines.push("它没有任何记录，会被彻底删除。");
    }
    if (!confirm(lines.join("\n"))) return;
    try {
      const r = await api("/api/channels/" + cid, { method: "DELETE" });
      toast(r.message || "已删除", "ok");
      await loadState();
      if (S.tab === "report") await loadRecent();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  /* ----------------------------------------------------------
     拖拽排序
     ---------------------------------------------------------- */

  let dragState = null;

  function serializeOrder() {
    return $$("#boardGrid .group-body").map((b) =>
      b.dataset.groupId + ":" + $$(".card", b).map((c) => c.dataset.channel).join(",")
    ).join("|");
  }

  let dragGroup = null;

  function groupDragStart(e) {
    const head = e.target.closest(".group-head");
    if (!head || !S.canDrag) return;
    if (e.target.closest("button,input,select,a")) { e.preventDefault(); return; }
    dragGroup = head.parentElement;
    dragGroup.classList.add("group-dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", dragGroup.dataset.gid || "0"); } catch (err) { /* ie */ }
    }
  }

  function groupDragOver(e) {
    if (!dragGroup) return;
    const head = e.target.closest(".group-head");
    if (!head) return;
    const target = head.parentElement;
    if (!target || target === dragGroup) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    const r = target.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 3;
    if (before) {
      if (target.previousElementSibling === dragGroup) return;
      target.parentElement.insertBefore(dragGroup, target);
    } else {
      if (target.nextElementSibling === dragGroup) return;
      target.parentElement.insertBefore(dragGroup, target.nextElementSibling);
    }
  }

  async function groupDrop(e) {
    if (!dragGroup) return;
    e.preventDefault();
    dragGroup.classList.remove("group-dragging");
    const el = dragGroup;
    dragGroup = null;
    const ids = Array.prototype.slice.call(document.querySelectorAll("#boardGrid .group"))
      .map((g) => Number(g.dataset.gid))
      .filter((v) => v > 0);
    if (!ids.length) return;
    try {
      await api("/api/groups/reorder", { method: "POST", body: { ids: ids } });
      toast("分组顺序已保存");
    } catch (err) {
      toast(err.message, "err");
      el.classList.remove("group-dragging");
    }
  }

  function boardDragStart(e) {
    // 通道位置固定，不再支持拖动换组；分组拖动走 groupDragStart
    e.preventDefault();
    const pressed = S.lastMouseDown;
    if (pressed && pressed.closest("input,button,textarea,select,a")) {
      e.preventDefault();
      return;
    }
    dragState = { el: card, id: Number(card.dataset.channel), startOrder: serializeOrder() };
    card.classList.add("dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", card.dataset.channel); } catch (err) { /* ie */ }
    }
  }

  function boardDragOver(e) {
    if (!dragState) return;
    const head = e.target.closest(".group-head");
    const body = e.target.closest(".group-body");

    if (head) {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
      const targetBody = head.parentElement.querySelector(".group-body");
      if (targetBody && !targetBody.contains(dragState.el)) targetBody.appendChild(dragState.el);
      return;
    }
    if (!body) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";

    const over = e.target.closest(".card");
    if (!over) {
      // 悬停在组的空白处：放到该组末尾
      if (!body.contains(dragState.el)) body.appendChild(dragState.el);
      return;
    }
    if (over === dragState.el) return;

    const r = over.getBoundingClientRect();
    let before;
    if (e.clientY < r.top) before = true;
    else if (e.clientY > r.bottom) before = false;
    else before = e.clientX < r.left + r.width / 2;

    const sameParent = over.parentElement === dragState.el.parentElement;
    if (before) {
      if (sameParent && over.previousElementSibling === dragState.el) return;
      over.parentElement.insertBefore(dragState.el, over);
    } else {
      if (sameParent && over.nextElementSibling === dragState.el) return;
      over.parentElement.insertBefore(dragState.el, over.nextElementSibling);
    }
  }

  function boardDragEnd() {
    if (!dragState) return;
    const el = dragState.el;
    const changed = serializeOrder() !== dragState.startOrder;
    el.classList.remove("dragging");
    dragState = null;
    if (changed) persistBoardOrder();
  }

  async function persistBoardOrder() {
    const payload = [];
    $$("#boardGrid .group-body").forEach((body) => {
      payload.push({
        group_id: Number(body.dataset.groupId) || 0,
        ids: $$(".card", body).map((c) => Number(c.dataset.channel)),
      });
    });
    if (payload.length === 0) return;
    try {
      await api("/api/channels/reorder", { method: "POST", body: { groups: payload } });
      toast("顺序已保存", "ok");
    } catch (e) {
      toast("保存顺序失败：" + e.message, "err");
    }
    await loadState();
  }

  /* ============================================================
     报送页
     ============================================================ */

  function renderReportChannels() {
    const sel = $("#reportChannel");
    const chs = S.data ? S.data.channels : [];
    if (chs.length === 0) {
      sel.innerHTML = '<option value="">（暂无通道，请先去「设置」添加）</option>';
      sel.disabled = true;
      S.reportChannelId = null;
      renderReportForm();
      return;
    }
    sel.disabled = false;
    if (!S.reportChannelId || !chs.some((c) => c.id === S.reportChannelId)) {
      S.reportChannelId = chs[0].id;
    }
    sel.innerHTML = chs.map((c) =>
      '<option value="' + c.id + '"' + (c.id === S.reportChannelId ? " selected" : "") + ">" +
      esc(c.code) + (c.name ? " · " + esc(c.name) : "") +
      (c.current ? "  【" + esc(c.current.battery.code) + "】" : "  （空）") +
      "</option>"
    ).join("");
    renderReportForm();
  }

  function renderReportForm() {
    const ch = channelById(S.reportChannelId);
    const banner = $("#reportBattery");
    if (!ch) {
      banner.className = "battery-banner empty";
      banner.innerHTML = "请先添加通道";
    } else if (!ch.current) {
      banner.className = "battery-banner empty";
      banner.innerHTML = "该通道当前<b>没有电池</b>，去 <a href='#' data-act='go-board'>看板</a> 装一颗";
    } else {
      const b = ch.current.battery;
      banner.className = "battery-banner";
      banner.innerHTML =
        "当前电池 <b>" + esc(b.code) + "</b>" +
        '<span class="badge">' + (b.batch ? "批次 " + esc(b.batch) : "无批次") + "</span>" +
        '<span class="badge">已装 ' + esc(durText(ch.current.started_at)) + "</span>";
    }

    const fields = (S.data ? S.data.fields : []).filter((f) => f.active);
    $("#reportFields").innerHTML = fields.map((f) => {
      const id = "rf_" + f.key;
      let input;
      if (f.kind === "select") {
        const opts = (f.options || "").split(/[,，]/).filter(Boolean);
        input = '<select id="' + id + '" class="input"><option value=""></option>' +
          opts.map((o) => '<option value="' + esc(o) + '">' + esc(o) + "</option>").join("") + "</select>";
      } else if (f.kind === "text") {
        input = '<input id="' + id + '" class="input" placeholder="' + esc(f.unit || "") + '">';
      } else {
        input = '<input id="' + id + '" class="input" type="number" step="any" placeholder="' +
          esc(f.unit || "") + '">';
      }
      return '<div class="form-row"><label>' + esc(f.label) +
        (f.unit ? ' <span class="hint">(' + esc(f.unit) + ")</span>" : "") + "</label>" + input + "</div>";
    }).join("");

    // 状态由装机情况自动判定：装了电池就是运行中，没装就是停机
    const curCh = ((S.data && S.data.channels) || []).find((c) => c.id === S.reportChannelId);
    const autoStatus = curCh && curCh.current ? "运行中" : "停机";
    $("#reportStatus").innerHTML = '<option value="' + esc(autoStatus) + '">' +
      esc(autoStatus) + "（自动判定）</option>";
    $("#reportStatus").disabled = true;
    $("#reportTime").value = nowLocal();
  }

  async function submitReport() {
    const chId = S.reportChannelId;
    if (!chId) { toast("请先添加并选择通道", "err"); return; }
    const values = {};
    (S.data.fields || []).filter((f) => f.active).forEach((f) => {
      const el = $("#rf_" + f.key);
      if (el && el.value !== "") values[f.key] = el.value;
    });
    if (Object.keys(values).length === 0 && !$("#reportNote").value.trim()) {
      toast("至少填一个数值，或写点备注", "err");
      return;
    }
    const btn = $("#btnSubmitReport");
    btn.disabled = true;
    try {
      await api("/api/snapshots", {
        method: "POST",
        body: {
          channel_id: chId,
          values: values,
          status: $("#reportStatus").value,
          note: $("#reportNote").value,
          taken_at: $("#reportTime").value,
          source: "web",
        },
      });
      toast("已保存", "ok");
      $("#reportNote").value = "";
      (S.data.fields || []).filter((f) => f.active).forEach((f) => {
        const el = $("#rf_" + f.key);
        if (el) el.value = "";
      });
      $("#reportTime").value = nowLocal();
      await loadState();
      await loadRecent();
      await loadTests();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  async function loadRecent() {
    try {
      const d = await api("/api/snapshots?limit=20");
      S.cmpSnaps = d.items;
      renderRecent(d.items);
    } catch (e) { /* ignore */ }
  }

  function renderRecent(items) {
    const box = $("#recentList");
    if (!items.length) {
      box.innerHTML = '<div class="empty small"><p>还没有报送记录</p></div>';
      return;
    }
    const fields = (S.data.fields || []).filter((f) => f.active);
    box.innerHTML = items.map((s) => {
      const vmap = {};
      (s.values || []).forEach((v) => { vmap[v.key] = v; });
      const vals = fields.filter((f) => vmap[f.key]).map((f) =>
        esc(f.label) + " " + esc(fmtVal(vmap[f.key].value)) + (f.unit || "")
      ).join(" · ");
      return (
        '<div class="recent-item">' +
          '<span class="t">' + esc(fmtTime(s.taken_at)) + "</span>" +
          '<div class="body">' +
            '<div class="line">' +
              '<span class="chip blue">' + esc(s.channel_code) + "</span>" +
              (s.battery_code ? '<span class="badge">' + esc(s.battery_code) + "</span>" : "") +
              (s.status ? '<span class="chip ' + statusClass(s.status) + '">' + esc(s.status) + "</span>" : "") +
            "</div>" +
            (vals ? '<div class="vals">' + vals + "</div>" : "") +
            (s.note ? '<div class="note">' + esc(s.note) + "</div>" : "") +
          "</div>" +
        "</div>"
      );
    }).join("");
  }

  /* ============================================================
     换电池 / 装电池 / 下机
     ============================================================ */

  async function openSwap(channelId) {
    const ch = channelById(channelId);
    if (!ch) return;
    const cur = ch.current;
    let codes = [];
    try {
      const d = await api("/api/batteries?limit=500");
      codes = d.items.map((b) => b.code);
    } catch (e) { /* ignore */ }
    $("#batteryCodes").innerHTML = codes.map((c) => '<option value="' + esc(c) + '">').join("");

    const body =
      (cur
        ? '<div class="battery-banner">当前电池 <b>' + esc(cur.battery.code) + "</b>" +
          '<span class="badge">已装 ' + esc(durText(cur.started_at)) + "</span></div>"
        : "") +
      (cur
        ? '<div class="form-row"><label>本次结果</label>' +
          '<select id="swResult" class="input">' +
          ["完成", "中途停止", "异常", "报废", "未完成"].map((o) => "<option>" + o + "</option>").join("") +
          "</select></div>" +
          '<div class="form-row"><label>旧电池下机时间</label>' +
          '<input type="datetime-local" id="swPrevEnd" class="input" value="' + nowLocal() + '"></div>'
        : "") +
      '<div class="form-row"><label>新电池编号 *</label>' +
      '<input id="swCode" class="input" list="batteryCodes" placeholder="例如 B0012，新编号会自动建档" autocomplete="off"></div>' +
      '<div class="form-grid">' +
        '<div class="form-row"><label>批次</label><input id="swBatch" class="input" placeholder="可选"></div>' +
        '<div class="form-row"><label>型号</label><input id="swModel" class="input" placeholder="可选"></div>' +
        '<div class="form-row"><label>额定容量(Ah)</label><input id="swCap" class="input" type="number" step="any" placeholder="可选"></div>' +
      "</div>" +
      '<div class="form-row"><label>上机时间</label>' +
      '<input type="datetime-local" id="swStart" class="input" value="' + nowLocal() + '"></div>' +
      '<div class="form-row"><label>备注</label>' +
      '<textarea id="swNote" class="input" rows="2" placeholder="可选"></textarea></div>';

    const foot =
      (cur ? '<button class="btn danger" id="swCheckout">只下机，不装新电池</button>' : "") +
      '<div class="spacer"></div>' +
      '<button class="btn" id="swCancel">取消</button>' +
      '<button class="btn primary" id="swOk">' + (cur ? "确认换电池" : "确认装入") + "</button>";

    openModal({
      title: ch.code + (cur ? " · 换电池" : " · 装入电池"),
      body: body,
      foot: foot,
      onOpen: () => {
        const codeEl = $("#swCode");
        codeEl.focus();
        const submit = async () => {
          const code = codeEl.value.trim();
          if (!code) { toast("请填写新电池编号", "err"); codeEl.focus(); return; }
          $("#swOk").disabled = true;
          try {
            const r = await api("/api/channels/" + channelId + "/swap", {
              method: "POST",
              body: {
                battery_code: code,
                batch: $("#swBatch").value.trim(),
                model: $("#swModel").value.trim(),
                rated_capacity: $("#swCap").value || null,
                started_at: $("#swStart").value,
                prev_ended_at: cur ? $("#swPrevEnd").value : null,
                result: cur ? $("#swResult").value : "",
                note: $("#swNote").value,
              },
            });
            closeModal();
            toast((cur ? "已换电池：" : "已装入：") + code + (r.battery_created ? "（新建档）" : ""), "ok");
            await loadState();
            if (S.tab === "report") await loadRecent();
          } catch (e) {
            toast(e.message, "err");
            $("#swOk").disabled = false;
          }
        };
        $("#swOk").onclick = submit;
        codeEl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
        $("#swCancel").onclick = closeModal;
        if (cur) {
          $("#swCheckout").onclick = async () => {
            if (!confirm("确认将 " + cur.battery.code + " 从 " + ch.code + " 上取下？")) return;
            try {
              await api("/api/channels/" + channelId + "/checkout", {
                method: "POST",
                body: { result: $("#swResult").value, ended_at: $("#swPrevEnd").value },
              });
              closeModal();
              toast("已下机", "ok");
              await loadState();
            } catch (e) { toast(e.message, "err"); }
          };
        }
      },
    });
  }

  /* ============================================================
     通道详情
     ============================================================ */

  async function openChannelDetail(channelId) {
    let d;
    try { d = await api("/api/channels/" + channelId + "/timeline"); }
    catch (e) { toast(e.message, "err"); return; }

    const ch = d.channel;
    const events = d.events || [];
    const head =
      '<div class="kv-grid">' +
        '<div class="kv"><div class="k">通道</div><div class="v">' + esc(ch.code) + "</div></div>" +
        '<div class="kv"><div class="k">装机次数</div><div class="v">' +
          events.filter((e) => e.type === "placement").length + "</div></div>" +
        '<div class="kv"><div class="k">报送条数</div><div class="v">' +
          events.filter((e) => e.type === "snapshot").length + "</div></div>" +
      "</div>";

    const tl = events.length === 0
      ? '<div class="empty small"><p>暂无历史记录</p></div>'
      : '<div class="timeline">' + events.map((e) => {
          if (e.type === "placement") {
            const done = !!e.end;
            return (
              '<div class="tl-item placement">' +
                '<div class="tl-time">' + esc(fmtTime(e.at)) + " → " + (done ? esc(fmtTime(e.end)) : "至今") +
                  " · " + esc(durText(e.at, e.end)) + "</div>" +
                '<div class="tl-title">' + (done ? "装机（已结束）" : "装机（进行中）") + "：电池 " + esc(e.battery_code) + "</div>" +
                '<div class="tl-desc">' +
                  (e.batch ? "批次 " + esc(e.batch) + "　" : "") +
                  (e.model ? "型号 " + esc(e.model) + "　" : "") +
                  (e.result ? '<span class="chip ' + (e.result === "完成" ? "ok" : "warn") + '">' + esc(e.result) + "</span>" : "") +
                  (e.note ? "<br>" + esc(e.note) : "") +
                "</div>" +
              "</div>"
            );
          }
          const vals = (e.values || []).map((v) =>
            esc(v.label) + " " + esc(fmtVal(v.value)) + (v.unit || "")
          ).join(" · ");
          return (
            '<div class="tl-item snapshot">' +
              '<div class="tl-time">' + esc(fmtTime(e.at)) + "</div>" +
              '<div class="tl-title">' +
                (e.status ? '<span class="chip ' + statusClass(e.status) + '">' + esc(e.status) + "</span>" : "报送") +
                (e.battery_code ? ' <span class="badge">' + esc(e.battery_code) + "</span>" : "") +
                (e.source === "cli" ? ' <span class="badge">代录</span>' : "") +
              "</div>" +
              (vals ? '<div class="tl-desc">' + vals + "</div>" : "") +
              (e.note ? '<div class="tl-desc muted">' + esc(e.note) + "</div>" : "") +
            "</div>"
          );
        }).join("") + "</div>";

    openModal({
      title: "通道 " + ch.code + (ch.name ? " · " + ch.name : ""),
      body: head + tl,
      foot: '<button class="btn" id="cdReport">报送状态</button>' +
            '<button class="btn primary" id="cdSwap">装/换电池</button>' +
            '<div class="spacer"></div><button class="btn" id="cdClose">关闭</button>',
      wide: true,
      onOpen: () => {
        $("#cdClose").onclick = closeModal;
        $("#cdSwap").onclick = () => { closeModal(); openSwap(channelId); };
        $("#cdReport").onclick = () => {
          closeModal();
          switchTab("report");
          S.reportChannelId = channelId;
          renderReportChannels();
        };
      },
    });
  }

  /* ============================================================
     电池档案
     ============================================================ */

  async function loadBatteries(q) {
    S.batteryQuery = q || "";
    try {
      const d = await api("/api/batteries?q=" + encodeURIComponent(S.batteryQuery) + "&limit=300");
      S.batteryList = d.items;
      renderBatteryList();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderBatteryList() {
    const box = $("#batteryList");
    if (!S.batteryList.length) {
      box.innerHTML = '<div class="empty small"><p>没有找到电池</p><p class="hint">装电池时会自动建档，或点右上角「登记电池」</p></div>';
      return;
    }
    box.innerHTML = S.batteryList.map((b) =>
      '<div class="battery-item' + (b.id === S.activeBatteryId ? " active" : "") + '" data-bid="' + b.id + '">' +
        "<div>" +
          '<div class="bcode">' + esc(b.code) + "</div>" +
          '<div class="bmeta">' + [b.batch ? "批次 " + esc(b.batch) : "", b.model ? esc(b.model) : ""].filter(Boolean).join(" · ") +
            (b.batch || b.model ? "" : "无批次信息") + "</div>" +
        "</div>" +
        '<div class="right">' +
          (b.current ? '<span class="chip ok">在机 ' + esc(b.current.channel_code) + "</span>"
                     : '<span class="chip none">空闲</span>') +
          '<span class="hint">' + b.placement_count + " 次装机</span>" +
        "</div>" +
      "</div>"
    ).join("");
  }

  async function openBatteryProfile(id) {
    S.activeBatteryId = id;
    renderBatteryList();
    let b;
    try { b = await api("/api/batteries/" + id); }
    catch (e) { toast(e.message, "err"); return; }

    const cur = (b.placements || []).find((p) => !p.ended_at);
    const kvs =
      '<div class="kv-grid">' +
        '<div class="kv"><div class="k">累计在机</div><div class="v">' + b.total_hours + " h</div></div>" +
        '<div class="kv"><div class="k">装机次数</div><div class="v">' + (b.placements || []).length + "</div></div>" +
        '<div class="kv"><div class="k">当前</div><div class="v">' + (cur ? esc(cur.channel_code) : "空闲") + "</div></div>" +
        '<div class="kv"><div class="k">报送</div><div class="v">' + (b.snapshots || []).length + "</div></div>" +
      "</div>";

    const plist = (b.placements || []).length
      ? '<div class="timeline">' + b.placements.map((p) =>
          '<div class="tl-item placement">' +
            '<div class="tl-time">' + esc(fmtTime(p.started_at)) + " → " + (p.ended_at ? esc(fmtTime(p.ended_at)) : "至今") +
              " · " + esc(durText(p.started_at, p.ended_at)) + "</div>" +
            '<div class="tl-title">通道 ' + esc(p.channel_code) + "</div>" +
            (p.result ? '<div class="tl-desc"><span class="chip ' + (p.result === "完成" ? "ok" : "warn") + '">' +
              esc(p.result) + "</span>" + (p.note ? " " + esc(p.note) : "") + "</div>" : "") +
          "</div>"
        ).join("") + "</div>"
      : '<div class="empty small"><p>还没有装机记录</p></div>';

    const snaps = (b.snapshots || []).length
      ? '<div class="table-wrap" style="max-height:340px"><table class="table"><thead><tr>' +
          "<th>时间</th><th>通道</th><th>状态</th>" +
          (S.data.fields || []).filter((f) => f.active).map((f) => "<th>" + esc(f.label) + "</th>").join("") +
          "<th>备注</th></tr></thead><tbody>" +
          b.snapshots.slice(0, 100).map((s) => {
            const vmap = {};
            (s.values || []).forEach((v) => { vmap[v.key] = v.value; });
            return "<tr><td>" + esc(fmtTime(s.taken_at)) + "</td><td>" + esc(s.channel_code) + "</td>" +
              "<td>" + (s.status ? '<span class="chip ' + statusClass(s.status) + '">' + esc(s.status) + "</span>" : "—") + "</td>" +
              (S.data.fields || []).filter((f) => f.active).map((f) =>
                '<td class="num">' + esc(fmtVal(vmap[f.key])) + "</td>").join("") +
              "<td>" + esc(s.note || "") + "</td></tr>";
          }).join("") +
          "</tbody></table></div>"
      : "";

    $("#batteryProfile").innerHTML =
      '<div class="block-head"><h2>' + esc(b.code) + "</h2>" +
        (b.batch ? '<span class="badge">批次 ' + esc(b.batch) + "</span>" : "") +
        (b.model ? '<span class="badge">' + esc(b.model) + "</span>" : "") +
        (b.rated_capacity ? '<span class="badge">' + esc(b.rated_capacity) + " Ah</span>" : "") +
      "</div>" +
      (b.note ? '<p class="hint" style="margin-bottom:10px">' + esc(b.note) + "</p>" : "") +
      kvs +
      '<h2 class="section-title">装机经过</h2>' + plist +
      (snaps ? '<h2 class="section-title">状态报送</h2>' + snaps : "");
  }

  async function openNewBattery() {
    openModal({
      title: "登记电池",
      body:
        '<div class="form-row"><label>电池编号 *</label><input id="nbCode" class="input" autocomplete="off"></div>' +
        '<div class="form-grid">' +
          '<div class="form-row"><label>批次</label><input id="nbBatch" class="input"></div>' +
          '<div class="form-row"><label>型号</label><input id="nbModel" class="input"></div>' +
          '<div class="form-row"><label>额定容量(Ah)</label><input id="nbCap" class="input" type="number" step="any"></div>' +
        "</div>" +
        '<div class="form-row"><label>备注</label><textarea id="nbNote" class="input" rows="2"></textarea></div>',
      foot: '<div class="spacer"></div><button class="btn" id="nbCancel">取消</button>' +
            '<button class="btn primary" id="nbOk">保存</button>',
      onOpen: () => {
        $("#nbCode").focus();
        $("#nbCancel").onclick = closeModal;
        $("#nbOk").onclick = async () => {
          const code = $("#nbCode").value.trim();
          if (!code) { toast("请填写电池编号", "err"); return; }
          try {
            const r = await api("/api/batteries", {
              method: "POST",
              body: {
                code: code,
                batch: $("#nbBatch").value.trim(),
                model: $("#nbModel").value.trim(),
                rated_capacity: $("#nbCap").value || null,
                note: $("#nbNote").value.trim(),
              },
            });
            closeModal();
            toast(r.existed ? "电池已存在，已更新信息" : "已登记", "ok");
            await loadBatteries(S.batteryQuery);
            await openBatteryProfile(r.battery.id);
            await loadState();
          } catch (e) { toast(e.message, "err"); }
        };
      },
    });
  }

  /* ============================================================
     对比 / 留痕
     ============================================================ */

  function renderCompareFilters() {
    const chs = S.data ? S.data.channels : [];
    const cur = $("#cmpChannel").value;
    $("#cmpChannel").innerHTML = '<option value="">全部通道</option>' +
      chs.map((c) => '<option value="' + c.id + '">' + esc(c.code) + "</option>").join("");
    $("#cmpChannel").value = cur || "";

    const opts = S.data ? S.data.settings.status_options : [];
    const cs = $("#cmpStatus").value;
    $("#cmpStatus").innerHTML = '<option value="">全部状态</option>' +
      opts.map((o) => '<option value="' + esc(o) + '">' + esc(o) + "</option>").join("");
    $("#cmpStatus").value = cs || "";

    const batches = Array.from(new Set(
      S.cmpItems.map((i) => i.batch).filter(Boolean)
    )).sort();
    const cb = $("#cmpBatch").value;
    $("#cmpBatch").innerHTML = '<option value="">全部批次</option>' +
      batches.map((b) => '<option value="' + esc(b) + '">' + esc(b) + "</option>").join("");
    $("#cmpBatch").value = cb || "";
  }

  async function loadCompare() {
    try {
      const [cmp, snaps] = await Promise.all([
        api("/api/compare"),
        api("/api/snapshots?" + snapQuery()),
      ]);
      S.cmpItems = cmp.items;
      S.cmpSnaps = snaps.items;
      renderCompareFilters();
      renderCompareTable();
      renderSnapTable();
    } catch (e) { toast(e.message, "err"); }
  }

  function snapQuery() {
    const p = new URLSearchParams();
    p.set("limit", "500");
    if ($("#cmpChannel").value) p.set("channel_id", $("#cmpChannel").value);
    if ($("#cmpBatch").value) p.set("batch", $("#cmpBatch").value);
    if ($("#cmpStatus").value) p.set("status", $("#cmpStatus").value);
    if ($("#cmpFrom").value) p.set("from", $("#cmpFrom").value + "T00:00:00");
    if ($("#cmpTo").value) p.set("to", $("#cmpTo").value + "T23:59:59");
    return p.toString();
  }

  function renderCompareTable() {
    const t = $("#cmpTable");
    const fields = (S.data.fields || []).filter((f) => f.active);
    const batch = $("#cmpBatch").value;
    let items = S.cmpItems;
    if (batch) items = items.filter((i) => (i.batch || "") === batch);

    if (!items.length) {
      t.innerHTML = '<tbody><tr><td class="empty-cell">还没有电池数据</td></tr></tbody>';
      return;
    }

    t.innerHTML =
      "<thead><tr><th>电池编号</th><th>批次</th><th>当前通道</th><th>状态</th><th>最后报送</th>" +
      fields.map((f) => "<th>" + esc(f.label) + (f.unit ? "<br><span class='hint'>" + esc(f.unit) + "</span>" : "") + "</th>").join("") +
      "</tr></thead><tbody>" +
      items.map((i) => {
        const vmap = {};
        ((i.last && i.last.values) || []).forEach((v) => { vmap[v.key] = v.value; });
        return "<tr>" +
          "<td><b>" + esc(i.code) + "</b></td>" +
          "<td>" + esc(i.batch || "—") + "</td>" +
          "<td>" + (i.channel_code ? '<span class="chip blue">' + esc(i.channel_code) + "</span>" : '<span class="chip none">空闲</span>') + "</td>" +
          "<td>" + (i.last && i.last.status ? '<span class="chip ' + statusClass(i.last.status) + '">' + esc(i.last.status) + "</span>" : "—") + "</td>" +
          "<td>" + esc(i.last ? fmtTime(i.last.taken_at) : "—") + "</td>" +
          fields.map((f) => '<td class="num">' + esc(fmtVal(vmap[f.key])) + "</td>").join("") +
          "</tr>";
      }).join("") + "</tbody>";
  }

  function renderSnapTable() {
    const t = $("#snapTable");
    const fields = (S.data.fields || []).filter((f) => f.active);
    const items = S.cmpSnaps;
    $("#cmpCount").textContent = items.length ? "共 " + items.length + " 条" : "";
    if (!items.length) {
      t.innerHTML = '<tbody><tr><td class="empty-cell">没有符合条件的记录</td></tr></tbody>';
      return;
    }
    t.innerHTML =
      "<thead><tr><th>时间</th><th>通道</th><th>电池</th><th>批次</th><th>状态</th>" +
      fields.map((f) => "<th>" + esc(f.label) + (f.unit ? "<br><span class='hint'>" + esc(f.unit) + "</span>" : "") + "</th>").join("") +
      "<th>备注</th><th>来源</th></tr></thead><tbody>" +
      items.map((s) => {
        const vmap = {};
        (s.values || []).forEach((v) => { vmap[v.key] = v.value; });
        return "<tr>" +
          "<td>" + esc(fmtTime(s.taken_at)) + "</td>" +
          "<td>" + esc(s.channel_code) + "</td>" +
          "<td>" + esc(s.battery_code || "—") + "</td>" +
          "<td>" + esc(s.battery_batch || "—") + "</td>" +
          "<td>" + (s.status ? '<span class="chip ' + statusClass(s.status) + '">' + esc(s.status) + "</span>" : "—") + "</td>" +
          fields.map((f) => '<td class="num">' + esc(fmtVal(vmap[f.key])) + "</td>").join("") +
          "<td>" + esc(s.note || "") + "</td>" +
          '<td><span class="badge">' + (s.source === "cli" ? "代录" : esc(s.source || "web")) + "</span></td>" +
          "</tr>";
      }).join("") + "</tbody>";
  }

  /* ============================================================
     设置
     ============================================================ */

  function renderSettings() {
    if (!S.data) return;
    // 已归档通道（通道的增删改现在都在看板上做）
    const archived = S.data.archived_channels || [];
    $("#channelAdmin").innerHTML = archived.length
      ? archived.map((c) =>
          '<div class="admin-item archived" data-cid="' + c.id + '">' +
            '<span class="grow"><b>' + esc(c.code) + "</b> " + esc(c.name || "") + "</span>" +
            '<span class="badge">' + (c.placements || 0) + " 装机 / " + (c.snapshots || 0) + " 报送</span>" +
            '<button class="btn sm" data-act="restorech">恢复</button>' +
          "</div>"
        ).join("")
      : '<div class="hint">没有归档的通道</div>';

    // 字段
    $("#fieldAdmin").innerHTML = (S.data.fields || []).map((f) =>
      '<div class="admin-item" data-fid="' + f.id + '">' +
        '<input class="input" style="width:100px;padding:5px 8px" value="' + esc(f.label) + '" data-k="label">' +
        '<input class="input sm" style="width:64px;padding:5px 8px" value="' + esc(f.unit) + '" placeholder="单位" data-k="unit">' +
        '<span class="hint">' + (f.kind === "number" ? "数值" : f.kind === "select" ? "选项" : "文本") + "</span>" +
        (f.active ? "" : '<span class="chip none">已停用</span>') +
        '<button class="btn sm" data-act="savef">保存</button>' +
        '<button class="icon-btn" data-act="delf" title="删除字段">✕</button>' +
      "</div>"
    ).join("") || '<div class="hint">还没有字段</div>';

    // 状态选项
    $("#statusOptions").value = (S.data.settings.status_options || []).join(", ");
    $("#statusPreview").innerHTML = (S.data.settings.status_options || []).map((o) =>
      '<span class="chip ' + statusClass(o) + '">' + esc(o) + "</span>"
    ).join("");
  }

  const TABS = ["board", "report", "battery", "compare", "methods", "tests", "expenses", "settings"];

  function switchTab(tab) {
    if (TABS.indexOf(tab) < 0) tab = "board";
    S.tab = tab;
    $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + tab));
    if (location.hash !== "#" + tab) {
      history.replaceState(null, "", "#" + tab);
    }
    if (tab === "compare") loadCompare();
    if (tab === "methods") loadMethods();
    if (tab === "tests") loadTests();
    if (tab === "expenses") loadExpenses();
    if (tab === "battery") loadBatteries(S.batteryQuery);
    if (tab === "report") { renderReportChannels(); loadRecent(); }
    if (tab === "settings") renderSettings();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ============================================================
     事件绑定
     ============================================================ */

  function bind() {
    // 防浏览器自动填充：把所有文本输入框标记成不记忆
    document.addEventListener("focusin", (e) => {
      const t = e.target;
      if (t && t.tagName === "INPUT" && !t.hasAttribute("autocomplete")) {
        t.setAttribute("autocomplete", "off");
      }
    });

    // tabs
    $("#tabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) switchTab(b.dataset.tab);
    });

    $("#btnRefresh").onclick = async () => {
      try { await loadState(); toast("已刷新", "ok"); }
      catch (e) { toast(e.message, "err"); }
    };

    // 看板
    $("#boardSearch").addEventListener("input", (e) => {
      S.boardQuery = e.target.value;
      renderBoard();
    });
    $("#boardFilter").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-f]");
      if (!b) return;
      S.boardFilter = b.dataset.f;
      $$("#boardFilter button").forEach((x) => x.classList.toggle("active", x === b));
      renderBoard();
    });
    // 分组：点名字改名、点 ✕ 删除
    $("#boardGrid").addEventListener("click", (e) => {
      const gname = e.target.closest("[data-edit='group']");
      if (gname) {
        e.preventDefault();
        startGroupRename(gname, Number(gname.dataset.gid));
        return;
      }
      const del = e.target.closest("[data-act='delGroup']");
      if (del) {
        e.preventDefault();
        deleteGroup(Number(del.dataset.gid));
        return;
      }

      const card = e.target.closest(".card");
      if (!card) return;
      const cid = Number(card.dataset.channel);
      const editEl = e.target.closest("[data-edit]");
      if (editEl) {
        e.preventDefault();
        startInlineEdit(editEl, cid, editEl.dataset.edit);
        return;
      }
      const act = e.target.closest("[data-act]");
      if (act) {
        if (act.dataset.act === "delChannel") { e.preventDefault(); deleteChannel(cid); return; }
        if (act.dataset.act === "swap") { openSwap(cid); return; }
        if (act.dataset.act === "openTest") {
          e.preventDefault();
          e.stopPropagation();
          openTestModal(Number(act.dataset.tid));
          return;
        }
        if (act.dataset.act === "uploadNdax") {
          e.preventDefault();
          e.stopPropagation();
          pickNdaxForChannel(cid);
          return;
        }
        if (act.dataset.act === "report") {
          switchTab("report");
          S.reportChannelId = cid;
          renderReportChannels();
          return;
        }
      }
      openChannelDetail(cid);
    });

    // 拖拽排序
    $("#boardGrid").addEventListener("mousedown", (e) => { S.lastMouseDown = e.target; });
    $("#boardGrid").addEventListener("dragstart", (e) => {
      if (e.target.closest(".group-head")) { groupDragStart(e); return; }
      boardDragStart(e);
    });
    $("#boardGrid").addEventListener("dragover", groupDragOver);
    $("#boardGrid").addEventListener("drop", groupDrop);
    $("#boardGrid").addEventListener("dragend", () => {
      if (dragGroup) { dragGroup.classList.remove("group-dragging"); dragGroup = null; }
    });
    $("#boardGrid").addEventListener("dragover", boardDragOver);
    $("#boardGrid").addEventListener("drop", (e) => { if (dragState) e.preventDefault(); });
    $("#boardGrid").addEventListener("dragend", boardDragEnd);

    $("#btnAddGroup").onclick = openNewGroup;
    $("#btnAddChannel").onclick = openAddChannels;

    // 卡片密度切换（标准 / 紧凑）
    $("#densitySeg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-d]");
      if (b) setDensity(b.dataset.d);
    });

    // 报送
    $("#reportChannel").addEventListener("change", (e) => {
      S.reportChannelId = Number(e.target.value) || null;
      renderReportForm();
    });
    $("#btnSubmitReport").onclick = submitReport;
    document.addEventListener("click", (e) => {
      const a = e.target.closest("[data-act='go-board']");
      if (a) { e.preventDefault(); switchTab("board"); }
    });

    // 电池
    $("#batterySearch").addEventListener("keydown", (e) => {
      if (e.key === "Enter") loadBatteries(e.target.value);
    });
    $("#batterySearch").addEventListener("input", (e) => {
      if (e.target.value === "") loadBatteries("");
    });
    $("#btnSearchBattery").onclick = () => loadBatteries($("#batterySearch").value);
    $("#btnNewBattery").onclick = openNewBattery;
    $("#batteryList").addEventListener("click", (e) => {
      const item = e.target.closest("[data-bid]");
      if (item) openBatteryProfile(Number(item.dataset.bid));
    });

    // 对比
    $("#btnCmpQuery").onclick = loadCompare;
    $("#btnExportSnap").onclick = () => { window.location = "/api/export/snapshots.csv?" + snapQuery(); };
    $("#btnExportPlace").onclick = () => { window.location = "/api/export/placements.csv"; };
    $("#btnExpSnap2").onclick = () => { window.location = "/api/export/snapshots.csv?" + snapQuery(); };
    $("#btnExpPlace2").onclick = () => { window.location = "/api/export/placements.csv"; };
    $("#btnReset").onclick = async () => {
      const ans = prompt("这会删除全部通道、电池、装机与报送记录。\n确认请输入「清空」两个字：");
      if (ans === null) return;
      if (ans.trim() !== "清空") { toast("未确认，已取消", "err"); return; }
      try {
        const r = await api("/api/reset", { method: "POST", body: { confirm: "清空" } });
        toast(r.message, "ok");
        S.activeBatteryId = null;
        S.reportChannelId = null;
        await loadState();
      } catch (e) { toast(e.message, "err"); }
    };

    // 设置：已归档通道的恢复
    $("#channelAdmin").addEventListener("click", async (e) => {
      const row = e.target.closest("[data-cid]");
      if (!row) return;
      const cid = Number(row.dataset.cid);
      const act = e.target.closest("[data-act]");
      if (!act) return;
      if (act.dataset.act === "restorech") {
        try {
          await api("/api/channels/" + cid, { method: "PUT", body: { archived: 0 } });
          toast("通道已恢复", "ok");
          await loadState();
        } catch (err) { toast(err.message, "err"); }
      }
    });

    // 设置：字段
    $("#btnAddField").onclick = async () => {
      const label = $("#nfLabel").value.trim();
      if (!label) { toast("请填写字段名", "err"); return; }
      try {
        await api("/api/fields", {
          method: "POST",
          body: { label: label, unit: $("#nfUnit").value.trim(), kind: $("#nfKind").value },
        });
        $("#nfLabel").value = "";
        $("#nfUnit").value = "";
        toast("字段已添加", "ok");
        await loadState();
      } catch (e) { toast(e.message, "err"); }
    };
    $("#fieldAdmin").addEventListener("click", async (e) => {
      const row = e.target.closest("[data-fid]");
      if (!row) return;
      const fid = Number(row.dataset.fid);
      const act = e.target.closest("[data-act]");
      if (!act) return;
      if (act.dataset.act === "savef") {
        try {
          await api("/api/fields/" + fid, {
            method: "PUT",
            body: { label: $("[data-k='label']", row).value.trim(), unit: $("[data-k='unit']", row).value.trim() },
          });
          toast("已保存", "ok");
          await loadState();
        } catch (err) { toast(err.message, "err"); }
      } else if (act.dataset.act === "delf") {
        if (!confirm("删除这个字段？该字段已有的历史数值也会一并删除。")) return;
        try {
          await api("/api/fields/" + fid, { method: "DELETE" });
          toast("已删除", "ok");
          await loadState();
        } catch (err) { toast(err.message, "err"); }
      }
    });

    // 设置：状态选项
    $("#btnSaveStatus").onclick = async () => {
      try {
        await api("/api/settings", { method: "PUT", body: { status_options: $("#statusOptions").value } });
        toast("已保存", "ok");
        await loadState();
      } catch (e) { toast(e.message, "err"); }
    };

    // 弹窗
    $("#modalClose").onclick = closeModal;
    $("#modal").addEventListener("click", (e) => {
      if (e.target.id === "modal") closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });
  }

  /* ---------------------------------------------------------- 分组折叠 */

  function collapsedMap(key) {
    try { return JSON.parse(localStorage.getItem(key) || "{}") || {}; }
    catch (e) { return {}; }
  }

  function isCollapsed(key, gid) {
    return !!collapsedMap(key)[String(gid)];
  }

  function setCollapsed(key, gid, on) {
    const m = collapsedMap(key);
    if (on) m[String(gid)] = 1; else delete m[String(gid)];
    try { localStorage.setItem(key, JSON.stringify(m)); } catch (e) {}
  }

  function bindCollapse(root, key, sectionSel, headSel) {
    root.querySelectorAll(headSel).forEach((h) => {
      h.classList.add("is-toggle");
      h.onclick = (e) => {
        // 点到重命名、删除按钮时不触发折叠
        if (e.target.closest("[data-edit],[data-act],[data-gdel]")) return;
        const sec = h.closest(sectionSel);
        if (!sec) return;
        const on = !sec.classList.contains("collapsed");
        sec.classList.toggle("collapsed", on);
        setCollapsed(key, sec.dataset.gid, on);
      };
    });
  }

  /* ---------------------------------------------------------- 药品台账 */

  const DRUG = { items: [], q: "", category: "", loaded: false };

  function switchExpSub(name) {
    const isExp = name !== "drug";
    $$("#expSubTabs button").forEach((b) => b.classList.toggle("active", b.dataset.sub === (isExp ? "exp" : "drug")));
    $("#subExp").hidden = !isExp;
    $("#subDrug").hidden = isExp;
    if (!isExp) loadDrugs();
  }

  async function loadDrugs(force) {
    if (DRUG.loaded && !force) { renderDrugs(); return; }
    try {
      const r = await api("/api/drugs?q=" + encodeURIComponent(DRUG.q) +
        "&category=" + encodeURIComponent(DRUG.category));
      DRUG.items = r.items || [];
      DRUG.loaded = true;
      renderDrugs(r);
    } catch (e) { toast(e.message, "err"); }
  }

  function renderDrugs(meta) {
    const box = $("#drugList");
    if (!DRUG.items.length) {
      box.innerHTML = '<div class="empty"><p>' +
        (DRUG.q || DRUG.category ? "没有匹配的药品" : "还没有药品记录，点右上角「＋ 药品登记」") + "</p></div>";
      return;
    }
    const counts = {};
    DRUG.items.forEach((d) => { counts[d.category] = (counts[d.category] || 0) + 1; });
    const expired = DRUG.items.filter((d) => d.expired).length;
    const expiring = DRUG.items.filter((d) => d.expiring).length;

    const rows = DRUG.items.map((d) => {
      let badge = "";
      if (d.expired) badge = '<span class="drug-badge expired">已过期</span>';
      else if (d.expiring) badge = '<span class="drug-badge soon">' + d.days_left + " 天到期</span>";
      return '<tr' + (d.expired ? ' class="drug-row-expired"' : "") + ">" +
        '<td class="nowrap muted">' + esc(d.category) + "</td>" +
        '<td><b class="drug-name" data-copyname="' + esc(d.name) + '" title="点击复制名称，去采购平台搜索">' +
          esc(d.name) + "</b>" + badge +
          (d.location ? '<div class="muted" style="font-size:12px">' + esc(d.location) + "</div>" : "") +
        "</td>" +
        '<td class="nowrap">' + esc(d.purity || "—") + "</td>" +
        '<td class="nowrap">' + esc(d.vendor || "—") + "</td>" +
        '<td class="nowrap">' + esc(d.spec || "—") + "</td>" +
        '<td class="nowrap muted">' + esc(d.purchase_date || "—") + "</td>" +
        '<td class="nowrap muted">' + esc(d.expire_date || "—") + "</td>" +
        '<td class="nowrap muted">' + esc(d.author || "") + "</td>" +
        '<td><button class="icon-btn" data-drugdel="' + d.id + '" title="删除">✕</button></td>' +
      "</tr>";
    }).join("");

    box.innerHTML =
      '<div class="exp-sum">共 <strong>' + DRUG.items.length + "</strong> 种" +
        (expired ? '　<span class="drug-badge expired">' + expired + " 种已过期</span>" : "") +
        (expiring ? '　<span class="drug-badge soon">' + expiring + " 种 60 天内到期</span>" : "") +
      "</div>" +
      '<div class="table-wrap"><table class="table">' +
        "<thead><tr><th>类别</th><th>药品</th><th>纯度</th><th>厂家</th><th>规格</th>" +
        "<th>购买时间</th><th>有效期至</th><th>登记人</th><th></th></tr></thead>" +
        "<tbody>" + rows + "</tbody></table></div>";

    box.querySelectorAll("[data-copyname]").forEach((el) => {
      el.onclick = async () => {
        const ok = await copyText(el.dataset.copyname);
        toast(ok === false ? "复制失败，请长按手动复制" : "已复制「" + el.dataset.copyname + "」，去采购平台粘贴搜索", ok === false ? "err" : "ok");
      };
    });

    box.querySelectorAll("[data-drugdel]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("删除这条药品记录？")) return;
        try {
          await api("/api/drugs/" + b.dataset.drugdel, { method: "DELETE" });
          await loadDrugs(true);
          toast("已删除");
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  function drugEditor() {
    openModal({
      title: "药品登记",
      body:
        '<div class="form-row"><label>类别</label><select id="dgCat" class="input">' +
          ['固体', '液体', '手套箱固体', '手套箱液体'].map((c) => '<option value="' + c + '">' + c + "</option>").join("") +
        "</select></div>" +
        '<div class="form-row"><label>药品名称</label><input id="dgName" class="input" placeholder="例如：硫酸铝(十八水)">' +
          '<div class="hint-line" id="dgDup"></div></div>' +
        '<div class="form-row"><label>纯度</label><input id="dgPurity" class="input" placeholder="例如：分析纯AR"></div>' +
        '<div class="form-row"><label>厂家</label><input id="dgVendor" class="input" placeholder="例如：阿拉丁"></div>' +
        '<div class="form-row"><label>规格</label><input id="dgSpec" class="input" placeholder="例如：500g"></div>' +
        '<div class="form-row"><label>购买时间</label><input id="dgBuy" class="input" type="date"></div>' +
        '<div class="form-row"><label>有效期至</label><input id="dgExp" class="input" type="date">' +
          '<div class="hint-line">选填，填了会在快过期时提醒</div></div>' +
        '<div class="form-row"><label>存放位置</label><input id="dgLoc" class="input" placeholder="选填，如：手套箱 A"></div>',
      foot: '<button class="btn ghost" data-close>取消</button><button class="btn primary" id="dgSave">保存</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#dgName").focus();

        let timer = null;
        const checkDup = () => {
          clearTimeout(timer);
          timer = setTimeout(async () => {
            const nm = $("#dgName").value.trim();
            if (!nm) { $("#dgDup").textContent = ""; return; }
            try {
              const r = await api("/api/drugs/check?name=" + encodeURIComponent(nm));
              const list = r.items || [];
              if (!list.length) { $("#dgDup").innerHTML = ""; return; }
              $("#dgDup").innerHTML = '<span style="color:#b45309">已有 ' + list.length + " 条：</span>" +
                list.slice(0, 3).map((d) => esc(d.category + " / " + (d.vendor || "—") + " / " + (d.spec || "—"))).join("；") +
                (list.length > 3 ? "…" : "") + "　确认要重复购买？";
            } catch (e) { /* 忽略 */ }
          }, 300);
        };
        $("#dgName").oninput = checkDup;

        $("#dgSave").onclick = async () => {
          const name = $("#dgName").value.trim();
          if (!name) { toast("请填药品名称", "err"); return; }
          $("#dgSave").disabled = true;
          try {
            const r = await api("/api/drugs", { method: "POST", body: {
              category: $("#dgCat").value, name: name,
              purity: $("#dgPurity").value.trim(), vendor: $("#dgVendor").value.trim(),
              spec: $("#dgSpec").value.trim(), purchase_date: $("#dgBuy").value,
              expire_date: $("#dgExp").value, location: $("#dgLoc").value.trim() } });
            closeModal();
            DRUG.loaded = false;
            await loadDrugs(true);
            const n = (r.same_name || []).length;
            toast(n ? "已登记（已有 " + (n + 1) + " 条同名）" : "已登记", "ok");
          } catch (e) { toast(e.message, "err"); $("#dgSave").disabled = false; }
        };
      },
    });
  }

  /* ---------------------------------------------------------- 组内报账 */

  const EXP = { items: [], total: 0, q: "", month: "", scope: "pending", loaded: false };

  function canUseExpenses() {
    return !!(ME && (ME.is_admin || (ME.user && ME.user.beta)));
  }

  async function loadExpenses(force) {
    if (!canUseExpenses()) return;
    if (EXP.loaded && !force) { renderExpenses(); return; }
    try {
      const r = await api("/api/expenses?q=" + encodeURIComponent(EXP.q) +
        "&month=" + encodeURIComponent(EXP.month) + "&scope=" + EXP.scope);
      EXP.items = r.items || [];
      EXP.total = r.total || 0;
      EXP.loaded = true;
      renderExpenses();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderExpenses() {
    const box = $("#expList");
    if (!EXP.items.length) {
      box.innerHTML = '<div class="empty"><p>' +
        (EXP.q ? "没有匹配的记录" : "还没有报账记录，点右上角「＋ 记一笔」") + "</p></div>";
      return;
    }
    const rows = EXP.items.map((it) => {
      const pdf = (it.files || []).map((f) =>
        '<a class="exp-pdf" href="/expense-files/' + f.id + '" target="_blank" rel="noopener">PDF</a>').join(" ");
      const isAdmin = !!(ME && ME.is_admin);
      return '<tr' + (it.reimbursed ? ' class="exp-done"' : "") + '>' +
        '<td class="exp-check">' +
          (isAdmin
            ? '<input type="checkbox" data-reim="' + it.id + '"' +
              (it.reimbursed ? " checked" : "") + ' title="勾选＝已报销">'
            : '<span class="reim-mark"' + (it.reimbursed ? ' title="已报销"' : ' title="待报销"') + ">" +
              (it.reimbursed ? "✓" : "—") + "</span>") +
        "</td>" +
        '<td class="nowrap">' + esc(it.invoice_date) + "</td>" +
        "<td>" + esc(it.item) +
          (it.note ? '<div class="muted" style="font-size:12px">' + esc(it.note) + "</div>" : "") + "</td>" +
        '<td class="num">¥' + Number(it.amount || 0).toFixed(2) + "</td>" +
        '<td>' + (pdf || '<span class="muted">—</span>') + "</td>" +
        '<td class="muted nowrap">' + esc(it.author || "") + "</td>" +
        "<td>" + (isAdmin
          ? '<button class="icon-btn" data-expdel="' + it.id + '" title="删除">✕</button>'
          : "") + "</td>" +
      "</tr>";
    }).join("");

    box.innerHTML =
      '<div class="exp-sum">' + (EXP.scope === "pending" ? "待报销　" : "") +
        "合计 <strong>¥" + EXP.total.toFixed(2) + "</strong>　" +
        '<span class="muted">共 ' + EXP.items.length + " 笔</span></div>" +
      '<div class="table-wrap"><table class="table">' +
        "<thead><tr><th style=\"width:44px\">已报</th><th>开票日期</th><th>物品</th><th>金额</th><th>发票</th><th>录入</th><th></th></tr></thead>" +
        "<tbody>" + rows + "</tbody></table></div>";

    box.querySelectorAll("[data-reim]").forEach((cb) => {
      cb.onchange = async () => {
        const done = cb.checked;
        cb.disabled = true;
        try {
          await api("/api/expenses/" + cb.dataset.reim + "/reimburse",
            { method: "POST", body: { done: done } });
          toast(done ? "已标记为已报销" : "已撤销报销标记");
          await loadExpenses(true);
        } catch (e) { toast(e.message, "err"); cb.checked = !done; cb.disabled = false; }
      };
    });

    box.querySelectorAll("[data-expdel]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("删除这条报账？发票 PDF 也会一起删掉。")) return;
        try {
          await api("/api/expenses/" + b.dataset.expdel, { method: "DELETE" });
          await loadExpenses(true);
          toast("已删除");
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  // 开票信息：改这里就行
  const INVOICE = [
    ["户名", "西南石油大学"],
    ["纳税人识别号", "12510000452189430X"],
    ["地址", "四川省成都市新都区新都大道8号"],
    ["电话", "028-83032358"],
    ["开户行", "中国农业银行成都新都支行"],
    ["账号", "22824101040014888"],
  ];

  function copyText(text) {
    // 微信等老内核没有 clipboard API，退回 execCommand
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(() => legacyCopy(text));
    }
    return Promise.resolve(legacyCopy(text));
  }

  function legacyCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function showInvoice() {
    const rows = INVOICE.map((pair) =>
      '<div class="inv-row">' +
        '<span class="inv-k">' + esc(pair[0]) + "</span>" +
        '<span class="inv-v">' + esc(pair[1]) + "</span>" +
        '<button class="btn tiny ghost" data-copy="' + esc(pair[1]) + '">复制</button>' +
      "</div>").join("");
    const all = INVOICE.map((pair) => pair[0] + "：" + pair[1]).join("\n");

    openModal({
      title: "开票信息",
      body: '<div class="inv-box">' + rows + "</div>" +
            '<div class="hint-line" style="margin-top:10px">开票时按上面的信息填，点右侧可逐项复制</div>',
      foot: '<button class="btn ghost" data-close>关闭</button>' +
            '<button class="btn primary" id="invCopyAll">复制全部</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#modalFoot #invCopyAll").onclick = async () => {
          const ok = await copyText(all);
          toast(ok === false ? "复制失败，请长按手动复制" : "已复制全部开票信息", ok === false ? "err" : "ok");
        };
        $("#modalBody").querySelectorAll("[data-copy]").forEach((b) => {
          b.onclick = async () => {
            const ok = await copyText(b.dataset.copy);
            toast(ok === false ? "复制失败，请长按手动复制" : "已复制", ok === false ? "err" : "ok");
          };
        });
      },
    });
  }


  function openExportPick() {
    if (!EXP.items.length) { toast("当前没有记录可选", "err"); return; }
    const rows = EXP.items.map((it) =>
      '<label class="pick-row">' +
        '<input type="checkbox" checked data-pid="' + it.id + '">' +
        '<span class="pick-date">' + esc(it.invoice_date) + "</span>" +
        '<span class="pick-item">' + esc(it.item) + "</span>" +
        '<span class="pick-amt">¥' + Number(it.amount || 0).toFixed(2) + "</span>" +
        '<span class="pick-pdf">' +
          ((it.files || []).length ? '<span class="exp-pdf">PDF</span>' : '<span class="muted">—</span>') +
        "</span>" +
        (it.reimbursed ? '<span class="tag">已报</span>' : "") +
      "</label>").join("");

    openModal({
      title: "选择要打包的条目",
      wide: true,
      body:
        '<div class="pick-bar">' +
          '<button class="btn tiny" id="pickAll">全选</button>' +
          '<button class="btn tiny" id="pickNone">全不选</button>' +
          '<span class="spacer"></span>' +
          '<span id="pickInfo" class="muted"></span>' +
        "</div>" +
        '<div class="pick-list">' + rows + "</div>" +
        '<div class="hint-line">打包内容：报账汇总.csv + 每条对应的发票 PDF</div>',
      foot: '<button class="btn ghost" data-close>取消</button>' +
            '<button class="btn primary" id="pickGo">下载选中</button>',
      onOpen: () => {
        const box = $("#modalBody");
        const boxes = () => Array.prototype.slice.call(box.querySelectorAll("[data-pid]"));

        const refresh = () => {
          const chosen = boxes().filter((b) => b.checked);
          const sum = chosen.reduce((acc, b) => {
            const it = EXP.items.find((x) => String(x.id) === b.dataset.pid);
            return acc + Number((it && it.amount) || 0);
          }, 0);
          $("#pickInfo").textContent = "已选 " + chosen.length + " 条 · ¥" + sum.toFixed(2);
          const go = $("#pickGo");
          go.textContent = chosen.length ? "下载选中 " + chosen.length + " 条" : "下载选中";
          go.disabled = !chosen.length;
        };

        $("#pickAll").onclick = () => { boxes().forEach((b) => { b.checked = true; }); refresh(); };
        $("#pickNone").onclick = () => { boxes().forEach((b) => { b.checked = false; }); refresh(); };
        box.querySelectorAll("[data-pid]").forEach((b) => { b.onchange = refresh; });
        refresh();

        $("#modalFoot [data-close]").onclick = closeModal;
        $("#pickGo").onclick = () => {
          const ids = boxes().filter((b) => b.checked).map((b) => b.dataset.pid);
          if (!ids.length) return;
          location.href = "/api/expenses/export-zip?ids=" + ids.join(",");
          closeModal();
          toast("已开始下载 " + ids.length + " 条", "ok");
        };
      },
    });
  }

  function expenseEditor() {
    openModal({
      title: "记一笔报账",
      body:
        '<div class="form-row"><label>开票日期</label><input id="exDate" class="input" type="date"></div>' +
        '<div class="form-row"><label>物品</label><input id="exItem" class="input" placeholder="例如：称量纸"></div>' +
        '<div class="form-row"><label>金额（元）</label><input id="exAmount" class="input" type="number" step="0.01" placeholder="15.60"></div>' +
        '<div class="form-row"><label>备注</label><input id="exNote" class="input" placeholder="选填，比如订单号"></div>' +
        '<div class="form-row"><label>发票 PDF</label><input id="exPdf" type="file" accept="application/pdf">' +
          '<div class="hint-line">上传后自动命名为 <b>20290929-称量纸-15.60元.pdf</b>，不用自己改</div></div>',
      foot: '<button class="btn ghost" data-close>取消</button><button class="btn primary" id="exSave">保存</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#exDate").focus();
        $("#exSave").onclick = async () => {
          const date = $("#exDate").value;
          const item = $("#exItem").value.trim();
          const amount = parseFloat($("#exAmount").value);
          if (!date) { toast("请选开票日期", "err"); return; }
          if (!item) { toast("请填物品名称", "err"); return; }
          if (!(amount > 0)) { toast("金额要填大于 0 的数字", "err"); return; }
          $("#exSave").disabled = true;
          try {
            const r = await api("/api/expenses", { method: "POST", body: {
              invoice_date: date, item: item, amount: amount, note: $("#exNote").value.trim() } });
            const file = $("#exPdf").files[0];
            if (file) {
              const fd = new FormData();
              fd.append("file", file);
              const resp = await fetch("/api/expenses/" + r.id + "/files", {
                method: "POST", body: fd, credentials: "same-origin" });
              if (!resp.ok) {
                const t = await resp.text();
                throw new Error(t.indexOf("PDF") >= 0 ? "PDF 上传失败，检查文件格式" : "PDF 上传失败");
              }
            }
            closeModal();
            await loadExpenses(true);
            toast("已记录");
          } catch (e) { toast(e.message, "err"); $("#exSave").disabled = false; }
        };
      },
    });
  }

  /* ---------------------------------------------------------- 实验方法 */

  const MT = { list: [], current: null, cats: [], groups: [], q: "", cat: "", loaded: false };

  function fmtSize(n) {
    n = n || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function fmtDate(s) {
    if (!s) return "";
    return s.replace("T", " ").slice(0, 16);
  }

  async function loadMethods(force) {
    if (MT.loaded && !force) { renderMtList(); return; }
    try {
      if (!MT.cats.length) {
        MT.cats = (await api("/api/method-categories")).categories || [];
        $("#mtCategory").innerHTML = '<option value="">全部分类</option>' +
          MT.cats.map((c) => '<option value="' + esc(c) + '">' + esc(c) + "</option>").join("");
      }
      MT.groups = (await api("/api/method-groups")).groups || [];
      const q = encodeURIComponent(MT.q);
      const c = encodeURIComponent(MT.cat);
      const r = await api("/api/methods?q=" + q + "&category=" + c);
      MT.list = r.methods || [];
      MT.loaded = true;
      renderMtList();
      if (MT.current) {
        const still = MT.list.find((m) => m.id === MT.current.id);
        if (!still) { MT.current = null; renderMtBlank(); }
      }
    } catch (e) { toast(e.message, "err"); }
  }

  function mtItemHtml(m) {
    return '<div class="mt-item' + (MT.current && MT.current.id === m.id ? " active" : "") +
      '" data-mid="' + m.id + '">' +
      "<h4>" + esc(m.title) + "</h4>" +
      '<div class="mt-sub">' +
        (m.category ? '<span class="mt-chip cat">' + esc(m.category) + "</span>" : "") +
        (m.files ? '<span class="mt-chip">' + m.files + " 附件</span>" : "") +
        "<span>" + esc(fmtDate(m.updated_at)) + "</span>" +
      "</div></div>";
  }

  function renderMtList() {
    const box = $("#mtList");
    if (!MT.list.length) {
      box.innerHTML = '<div style="padding:26px 12px;text-align:center;color:var(--muted);font-size:13px">' +
        (MT.q || MT.cat ? "没有匹配的方法" : "还没有方法，点「＋ 新建」写第一篇") + "</div>";
      return;
    }

    const searching = !!(MT.q || MT.cat);
    let html = "";

    if (searching) {
      html = MT.list.map(mtItemHtml).join("");
    } else {
      // 按分组分区展示；没有归属的落到「未分组」
      const buckets = {};
      MT.groups.forEach((g) => { buckets[g.id] = []; });
      const loose = [];
      MT.list.forEach((m) => {
        if (m.group_id && buckets[m.group_id]) buckets[m.group_id].push(m);
        else loose.push(m);
      });
      MT.groups.forEach((g) => {
        const items = buckets[g.id] || [];
        if (!items.length) return;
        html += '<div class="mt-group' + (isCollapsed("bt.collapsedMt", g.id) ? " collapsed" : "") +
          '" data-gid="' + g.id + '">' +
          '<div class="mt-group-head">' +
            '<span class="group-caret"></span>' +
            "<span>" + esc(g.name) + "</span><em>" + items.length + "</em>" +
            '<button class="mt-gdel" data-gdel="' + g.id + '" title="删除分组">✕</button>' +
          '</div><div class="mt-group-body">' + items.map(mtItemHtml).join("") + "</div></div>";
      });
      if (loose.length) {
        html += '<div class="mt-group' + (isCollapsed("bt.collapsedMt", 0) ? " collapsed" : "") +
          '" data-gid="0"><div class="mt-group-head"><span class="group-caret"></span><span>未分组</span><em>' +
          loose.length + '</em></div><div class="mt-group-body">' + loose.map(mtItemHtml).join("") + "</div></div>";
      }
    }

    box.innerHTML = html;
    bindCollapse(box, "bt.collapsedMt", ".mt-group", ".mt-group-head");
    box.querySelectorAll("[data-mid]").forEach((el) => {
      el.onclick = () => openMethod(Number(el.dataset.mid));
    });
    box.querySelectorAll("[data-gdel]").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        const g = MT.groups.find((x) => x.id === Number(b.dataset.gdel));
        if (!confirm("删除分组「" + (g ? g.name : "") + "」？\n组里的方法会移到「未分组」，不会被删除。")) return;
        try {
          await api("/api/method-groups/" + b.dataset.gdel, { method: "DELETE" });
          await loadMethods(true);
          toast("分组已删除");
        } catch (err) { toast(err.message, "err"); }
      };
    });
  }

  function renderMtBlank() {
    $("#mtDetail").innerHTML =
      '<div class="mt-blank"><div class="mt-blank-icon">📘</div>' +
      "<p>从左侧选择一个方法查看</p>" +
      '<p class="muted">这里放实验方法、操作步骤、参数设置和经验总结，供后来的人参考</p></div>';
  }

  async function openMethod(id) {
    try {
      const r = await api("/api/methods/" + id);
      MT.current = r.method;
      renderMtList();
      renderMethodDetail();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderMethodDetail() {
    const m = MT.current;
    if (!m) return renderMtBlank();
    const files = m.attachments || [];
    const list = files.length
      ? files.map((f) =>
          '<div class="mt-file">' +
            '<span>📎</span>' +
            '<a href="/files/' + f.id + '" target="_blank" rel="noopener">' + esc(f.filename) + "</a>" +
            '<span class="sz">' + fmtSize(f.size) + "</span>" +
            '<button class="del" data-del="' + f.id + '" title="删除附件">✕</button>' +
          "</div>").join("")
      : '<p class="muted" style="font-size:12.5px">还没有附件</p>';

    $("#mtDetail").innerHTML =
      '<div class="mt-head">' +
        "<h2>" + esc(m.title) + "</h2>" +
        '<div class="mt-metas">' +
          (m.category ? '<span class="mt-chip cat">' + esc(m.category) + "</span>" : "") +
          (m.author ? "<span>记录：" + esc(m.author) + "</span>" : "") +
          "<span>更新：" + esc(fmtDate(m.updated_at)) + "</span>" +
          '<div class="mt-actions">' +
            '<button class="btn ghost" id="mtEdit">编辑</button>' +
            '<button class="btn ghost" id="mtDel">删除</button>' +
          "</div>" +
        "</div>" +
      "</div>" +
      (m.summary ? '<div class="mt-summary">' + esc(m.summary) + "</div>" : "") +
      '<div class="mt-content">' + (m.content ? mdToHtml(m.content) : '<p class="muted">（还没有正文）</p>') + "</div>" +
      '<div class="mt-files">' +
        "<h3>附件（" + files.length + "）</h3>" + list +
        '<div class="mt-drop" id="mtDrop">点击选择文件，或把文件拖到这里上传<br><span style="font-size:11px">图片可直接预览，PDF/Word 点击下载，单个不超过 30M</span></div>' +
        '<input type="file" id="mtFile" multiple hidden>' +
      "</div>";

    $("#mtEdit").onclick = () => methodEditor(m);
    $("#mtDel").onclick = () => deleteMethod(m);
    const drop = $("#mtDrop"), input = $("#mtFile");
    drop.onclick = () => input.click();
    input.onchange = () => { if (input.files.length) uploadFiles(m.id, input.files); input.value = ""; };
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault(); drop.classList.add("over");
    }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault(); drop.classList.remove("over");
    }));
    drop.addEventListener("drop", (e) => {
      if (e.dataTransfer.files.length) uploadFiles(m.id, e.dataTransfer.files);
    });
    $("#mtDetail").querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = async () => {
        const fid = b.dataset.del;
        const inUse = m.content && m.content.indexOf("/files/" + fid) >= 0;
        if (!confirm(inUse
          ? "这个附件正在正文里当图片用，删掉之后正文那张图会变成空白。\n\n建议先去正文删掉图片引用，再来删附件。仍要删除吗？"
          : "删除这个附件？")) return;
        try {
          await api("/api/methods/files/" + fid, { method: "DELETE" });
          await openMethod(m.id);
          toast("附件已删除");
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  async function uploadFiles(mid, files) {
    const fd = new FormData();
    for (let i = 0; i < files.length; i++) fd.append("file", files[i]);
    toast("正在上传 " + files.length + " 个文件…");
    try {
      const res = await fetch("/api/methods/" + mid + "/files", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "上传失败");
      await openMethod(mid);
      await loadMethods(true);
      toast("上传成功");
    } catch (e) { toast(e.message, "err"); }
  }

  async function deleteMethod(m) {
    if (!confirm("删除方法「" + m.title + "」？\n\n正文和 " + (m.attachments || []).length + " 个附件会一起删掉，无法恢复。")) return;
    try {
      await api("/api/methods/" + m.id, { method: "DELETE" });
      MT.current = null;
      MT.loaded = false;
      renderMtBlank();
      await loadMethods(true);
      toast("已删除");
    } catch (e) { toast(e.message, "err"); }
  }

  function methodEditor(rec) {
    const isNew = !rec;
    const m = rec || { title: "", category: "", summary: "", content: "", tags: "" };
    openModal({
      title: isNew ? "新建实验方法" : "编辑方法",
      wide: true,
      body:
        '<div class="mt-form">' +
          '<div class="form-row"><label>标题</label><input id="mfTitle" value="' + esc(m.title) + '" placeholder="例如：三电极体系组装与 CV 测试"></div>' +
          '<div class="form-row"><label>分组</label><select id="mfGroup"><option value="0">未分组</option>' +
            MT.groups.map(function (g) {
              return '<option value="' + g.id + '"' + (m.group_id === g.id ? " selected" : "") + '>' + esc(g.name) + '</option>';
            }).join("") + "</select></div>" +
          '<div class="form-row"><label>分类</label><input id="mfCat" list="mfCatList" value="' + esc(m.category || "") + '" placeholder="选一个或自己写">' +
            '<datalist id="mfCatList">' + MT.cats.map((c) => '<option value="' + esc(c) + '">').join("") + "</datalist></div>" +
          '<div class="form-row"><label>一句话简介</label><input id="mfSummary" value="' + esc(m.summary || "") + '" placeholder="这条方法解决什么问题"></div>' +
          '<div class="form-row"><label>正文</label>' +
            '<textarea id="mfContent" placeholder="支持简单格式：&#10;## 二级标题&#10;- 列表项&#10;**加粗**   `行内代码`&#10;```&#10;代码块&#10;```">' + esc(m.content || "") + "</textarea>" +
            '<div class="hint-line">支持 ## 标题、- 列表、**加粗**、`代码`、```代码块```</div></div>' +
          '<div class="form-row"><label>标签（用空格隔开，方便搜索）</label><input id="mfTags" value="' + esc(m.tags || "") + '" placeholder="CV EIS 扣电"></div>' +
        "</div>",
      foot: '<button class="btn ghost" data-close>取消</button><button class="btn" id="mfSave">保存</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#mfTitle").focus();
        $("#mfSave").onclick = async () => {
          const body = {
            title: $("#mfTitle").value.trim(),
            category: $("#mfCat").value.trim(),
            summary: $("#mfSummary").value.trim(),
            content: $("#mfContent").value,
            tags: $("#mfTags").value.trim(),
            group_id: Number($("#mfGroup").value) || 0,
          };
          if (!body.title) return toast("标题不能为空", "err");
          try {
            if (isNew) {
              const r = await api("/api/methods", { method: "POST", body: body });
              closeModal();
              MT.loaded = false;
              await loadMethods(true);
              await openMethod(r.id);
              toast("已创建，可以在下面上传附件");
            } else {
              await api("/api/methods/" + m.id, { method: "PUT", body: body });
              closeModal();
              MT.loaded = false;
              await loadMethods(true);
              await openMethod(m.id);
              toast("已保存");
            }
          } catch (e) { toast(e.message, "err"); }
        };
      },
    });
  }

  function mdToHtml(text) {
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let inList = false, inCode = false, codeBuf = [];
    const inline = (s) => s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      if (/^```/.test(raw.trim())) {
        if (inCode) {
          out.push("<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>");
          codeBuf = []; inCode = false;
        } else { closeList(); inCode = true; }
        continue;
      }
      if (inCode) { codeBuf.push(raw); continue; }
      // 单独一行的图片：![说明](/files/12)
      const img = raw.trim().match(/^!\[([^\]]*)\]\(([^)\s]+)\)$/);
      if (img) {
        closeList();
        out.push('<figure class="mt-fig"><a href="' + esc(img[2]) + '" target="_blank" rel="noopener">' +
          '<img src="' + esc(img[2]) + '" alt="' + esc(img[1]) + '" loading="lazy"></a>' +
          (img[1] ? "<figcaption>" + esc(img[1]) + "</figcaption>" : "") + "</figure>");
        continue;
      }
      const line = esc(raw);
      if (!line.trim()) { closeList(); continue; }
      let mo;
      if ((mo = line.match(/^###\s+(.*)$/))) { closeList(); out.push("<h3>" + inline(mo[1]) + "</h3>"); continue; }
      if ((mo = line.match(/^##\s+(.*)$/)))  { closeList(); out.push("<h2>" + inline(mo[1]) + "</h2>"); continue; }
      if ((mo = line.match(/^>\s?(.*)$/)))   { closeList(); out.push("<blockquote>" + inline(mo[1]) + "</blockquote>"); continue; }
      if ((mo = line.match(/^[-*]\s+(.*)$/))) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push("<li>" + inline(mo[1]) + "</li>");
        continue;
      }
      closeList();
      out.push("<p>" + inline(line) + "</p>");
    }
    if (inList) out.push("</ul>");
    if (inCode) out.push("<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>");
    return out.join("\n");
  }

  function bindMethodsUI() {
    let timer = null;
    $("#mtSearch").addEventListener("input", (e) => {
      MT.q = e.target.value.trim();
      clearTimeout(timer);
      timer = setTimeout(() => loadMethods(true), 260);
    });
    $("#mtCategory").addEventListener("change", (e) => {
      MT.cat = e.target.value;
      loadMethods(true);
    });
    $("#drugNew").onclick = () => drugEditor();
    $$("#expSubTabs button").forEach((b) => {
      b.onclick = () => switchExpSub(b.dataset.sub);
    });
    let drugTimer = null;
    $("#drugSearch").oninput = () => {
      clearTimeout(drugTimer);
      drugTimer = setTimeout(() => {
        DRUG.q = $("#drugSearch").value.trim();
        loadDrugs(true);
      }, 260);
    };
    $("#drugCat").onchange = () => {
      DRUG.category = $("#drugCat").value;
      loadDrugs(true);
    };

    $("#expNew").onclick = () => expenseEditor();
    const expQuery = () => "?q=" + encodeURIComponent(EXP.q) + "&month=" + encodeURIComponent(EXP.month) +
      "&scope=" + EXP.scope;
    $("#expExport").onclick = () => {
      // 带 Content-Disposition: attachment，浏览器会直接下载，不会离开页面
      location.href = "/api/expenses/export" + expQuery();
    };
    $("#expInvoice").onclick = () => showInvoice();
    $("#expExportZip").onclick = () => openExportPick();
    $("#expMonth").onchange = () => {
      EXP.month = $("#expMonth").value;
      loadExpenses(true);
    };
    $("#expScope").onchange = () => {
      EXP.scope = $("#expScope").value;
      loadExpenses(true);
    };
    let expTimer = null;
    $("#expSearch").oninput = () => {
      clearTimeout(expTimer);
      expTimer = setTimeout(() => {
        EXP.q = $("#expSearch").value.trim();
        loadExpenses(true);
      }, 260);
    };

    $("#mtNew").onclick = () => methodEditor(null);
    $("#mtNewGroup").onclick = async () => {
      const name = (prompt("新分组名称，例如：理论计算组") || "").trim();
      if (!name) return;
      try {
        await api("/api/method-groups", { method: "POST", body: { name: name } });
        await loadMethods(true);
        toast("分组已创建：" + name);
      } catch (e) { toast(e.message, "err"); }
    };
  }

  /* ---------------------------------------------------------- 充放电数据 */

  const TS = { list: [], current: null, loaded: false };

  function statCell(label, value) {
    return '<div class="ts-stat"><b>' + esc(String(value)) + "</b><span>" + esc(label) + "</span></div>";
  }

  async function loadTests(force) {
    if (TS.loaded && !force) { renderTsList(); return; }
    try {
      const r = await api("/api/tests");
      TS.list = r.tests || [];
      S.testMap = {};
      TS.list.forEach(function (t) { if (t.channel_id) S.testMap[t.channel_id] = t; });
      TS.loaded = true;
      renderTsList();
      if (S.tab === "board") renderBoard();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderTsList() {
    const box = $("#tsList");
    if (!TS.list.length) {
      box.innerHTML = '<div style="padding:26px 12px;text-align:center;color:var(--muted);font-size:13px">' +
        "还没有测试数据<br>点上面的按钮上传 .ndax</div>";
      return;
    }
    box.innerHTML = TS.list.map((t) =>
      '<div class="mt-item' + (TS.current && TS.current.id === t.id ? " active" : "") + '" data-tid="' + t.id + '">' +
        "<h4>" + esc(t.channel_code || t.filename) + "</h4>" +
        '<div class="mt-sub">' +
          '<span class="mt-chip cat">' + (t.cycles || 0) + " 圈</span>" +
          "<span>" + esc((t.start_at || "").slice(0, 10)) + "</span>" +
        "</div>" +
      "</div>").join("");
    box.querySelectorAll("[data-tid]").forEach((el) => {
      el.onclick = () => openTest(Number(el.dataset.tid));
    });
  }

  async function openTest(id) {
    try {
      const r = await api("/api/tests/" + id);
      TS.current = r.test;
      renderTsList();
      renderTestDetail();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderTestDetail(target) {
    const box = target ? $(target) : ($("#tsDetail") || $("#modalBody #tsDetail"));
    const t = TS.current;
    if (!t) return;
    const cyc = (t.cycle_rows || []).filter((c) => (c.discharge_mAh || 0) > 0);
    const first = cyc[0] || {};
    const last = cyc[cyc.length - 1] || {};
    const ces = cyc.map((c) => c.ce_pct).filter((v) => v != null);
    const avgCe = ces.length ? ces.reduce((a, b) => a + b, 0) / ces.length : null;

    box.innerHTML =
      '<div class="mt-head">' +
        "<h2>" + esc(t.channel_code || t.filename) + "</h2>" +
        '<div class="mt-metas">' +
          "<span>" + esc(t.filename) + "</span>" +
          "<span>" + esc((t.start_at || "").slice(0, 16)) + " → " + esc((t.end_at || "").slice(0, 16)) + "</span>" +
          '<select id="tsLink" title="关联到看板通道">' +
            '<option value="">未关联通道</option>' +
            (S.data && S.data.channels ? S.data.channels : []).map(function (c) {
              return '<option value="' + c.id + '"' + (t.channel_id === c.id ? ' selected' : '') + '>' + esc(c.code) + '</option>';
            }).join('') +
          '</select>' +
          '<div class="mt-actions"><button class="btn ghost" id="tsDel">删除</button></div>' +
        "</div>" +
      "</div>" +
      '<div class="ts-stats">' +
        statCell("循环", (t.cycles || 0) + " 圈") +
        statCell("首圈放电", first.discharge_mAh
          ? (t.mass_mg > 0
              ? (first.discharge_mAh / (t.mass_mg / 1000)).toFixed(0) + " mAh/g"
              : first.discharge_mAh.toFixed(0) + " mAh")
          : "—") +
        statCell("末圈保持", last.retention_pct != null ? last.retention_pct.toFixed(1) + "%" : "—") +
        statCell("平均库伦效率", avgCe != null ? avgCe.toFixed(1) + "%" : "—") +
        statCell("采样点", (t.rows || 0).toLocaleString()) +
        '<div class="ts-mass">' +
          '<label>活性物质</label>' +
          '<input id="tsMass" type="number" step="0.01" min="0" placeholder="mg" value="' +
            (t.mass_mg ? t.mass_mg : "") + '">' +
          '<span class="muted">mg，填了按比容量 mAh/g 显示</span>' +
          '<button class="btn tiny" id="tsSync" style="margin-left:auto">重新同步报送</button>' +
        "</div>" +
      "</div>" +
      '<div class="ts-chart-box"><div class="ts-chart-title">循环容量与容量保持率' +
        (t.mass_mg > 0 ? "（比容量）" : "") + "</div>" +
        '<canvas id="tsCycleChart"></canvas></div>' +
      '<div class="ts-chart-box"><div class="ts-chart-title">单圈充放电曲线' +
        '<select id="tsCycleSel"></select></div><canvas id="tsCurveChart"></canvas></div>' +
      '<div class="ts-chart-box"><div class="ts-chart-title">dQ/dV 曲线（同圈）</div>' +
        '<canvas id="tsDqdvChart"></canvas></div>';

    box.querySelector("#tsDel").onclick = async () => {
      if (!confirm("删除这条测试数据？曲线和循环数据会一起删掉。")) return;
      try {
        await api("/api/tests/" + t.id, { method: "DELETE" });
        TS.current = null;
        TS.loaded = false;
        await loadTests(true);
        box.innerHTML = '<div class="mt-blank"><div class="mt-blank-icon">📈</div>' +
          "<p>已删除</p></div>";
        toast("已删除");
      } catch (e) { toast(e.message, "err"); }
    };

    const link = box.querySelector("#tsLink");
    if (link) link.onchange = async () => {
      try {
        await api("/api/tests/" + t.id + "/channel", { method: "PUT",
          body: { channel_id: link.value ? Number(link.value) : null } });
        t.channel_id = link.value ? Number(link.value) : null;
        TS.loaded = false;
        await loadTests(true);
        toast("已更新关联通道");
      } catch (e) { toast(e.message, "err"); }
    };

    const massEl = box.querySelector("#tsMass");
    if (massEl) {
      massEl.onchange = async () => {
        try {
          await api("/api/tests/" + t.id + "/mass", { method: "PUT",
            body: { mass_mg: Number(massEl.value) || 0 } });
          t.mass_mg = Number(massEl.value) || 0;
          renderTestDetail(target);
          toast("已更新活性物质质量");
        } catch (e) { toast(e.message, "err"); }
      };
    }

    const syncBtn = box.querySelector("#tsSync");
    if (syncBtn) {
      syncBtn.onclick = async () => {
        syncBtn.disabled = true;
        try {
          const r = await api("/api/tests/" + t.id + "/sync", { method: "POST" });
          await loadState();
          toast("已同步第 " + r.synced.cycle + " 圈到看板", "ok");
        } catch (e) { toast(e.message, "err"); }
        finally { syncBtn.disabled = false; }
      };
    }

    drawCycleChart(box.querySelector("#tsCycleChart"), cyc, t.mass_mg || 0);

    const sel = box.querySelector("#tsCycleSel");
    sel.innerHTML = cyc.map((c) => '<option value="' + c.cycle + '">第 ' + c.cycle + " 圈</option>").join("");
    if (cyc.length) {
      const pick = cyc[Math.min(cyc.length - 1, Math.max(0, cyc.length - 1))];
      sel.value = pick.cycle;
      loadCurve(t.id, pick.cycle, t.mass_mg || 0);
      sel.onchange = () => loadCurve(t.id, Number(sel.value), t.mass_mg || 0);
    }
  }


  async function openTestModal(tid) {
    try {
      const r = await api("/api/tests/" + tid);
      TS.current = r.test;
      openModal({
        title: "充放电数据",
        wide: true,
        body: '<div id="tsDetail"></div>',
        foot: '<button class="btn ghost" data-close>关闭</button>',
        onOpen: () => {
          $("#modalFoot [data-close]").onclick = closeModal;
          renderTestDetail("#modalBody #tsDetail");
        },
      });
    } catch (e) { toast(e.message, "err"); }
  }

  function pickNdaxForChannel(cid) {
    const inp = document.createElement("input");
    inp.type = "file";
    inp.accept = ".ndax";
    inp.onchange = async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      toast("正在解析 " + f.name + "…");
      try {
        const resp = await fetch("/api/tests/upload?channel_id=" + cid,
          { method: "POST", body: fd, credentials: "same-origin" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "上传失败");
        toast("已导入 " + data.cycles + " 圈" +
          (data.removed_old ? "，清理旧数据 " + data.removed_old + " 份" : "") +
          (data.synced ? "，已同步第 " + data.synced.cycle + " 圈到报送" : ""), "ok");
        await loadState();
        await openTestModal(data.id);
      } catch (e) { toast(e.message, "err"); }
    };
    inp.click();
  }

  async function loadCurve(tid, cycle, mass) {
    try {
      const r = await api("/api/tests/" + tid + "/curve?cycle=" + cycle);
      drawCurveChart($("#tsCurveChart"), r.points || [], cycle, mass || 0);
      const d = await api("/api/tests/" + tid + "/dqdv?cycle=" + cycle);
      drawDqdvChart($("#tsDqdvChart"), d, mass || 0);
    } catch (e) { toast(e.message, "err"); }
  }

  function drawDqdvChart(cv, data, mass) {
    if (!cv) return;
    const ch = data.charge || [], dis = data.discharge || [];
    if (!ch.length && !dis.length) {
      const { ctx, w, h } = fitCanvas(cv, 230);
      ctx.fillStyle = "#98a2b3";
      ctx.font = "12px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("这一圈没有可用的 dQ/dV 数据", w / 2, h / 2);
      return;
    }
    const div = mass > 0 ? mass / 1000 : 1;
    const all = ch.concat(dis);
    const xs = all.map((p) => p[0]);
    const ys = all.map((p) => p[1] / div);
    let x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (x1 - x0 < 0.05) { x1 = x0 + 0.05; }
    if (y1 - y0 < 1e-6) { y1 = y0 + 1; }

    const { ctx, w, h } = fitCanvas(cv, 230);
    const pad = { l: 56, r: 22, t: 16, b: 30 };
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    const X = (v) => pad.l + (v - x0) / (x1 - x0) * iw;
    const Y = (v) => pad.t + ih - (v - y0) / (y1 - y0) * ih;

    axisFrame(ctx, pad, w, h, mass > 0 ? "dQ/dV mAh g⁻¹ V⁻¹" : "dQ/dV mAh/V");
    ctx.fillStyle = "#98a2b3";
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      ctx.fillText((y1 - (y1 - y0) * i / 4).toFixed(1), pad.l - 8, pad.t + ih * i / 4 + 4);
    }
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) {
      ctx.fillText((x0 + (x1 - x0) * i / 4).toFixed(2), pad.l + iw * i / 4, h - 10);
    }

    const line = (pts, color) => {
      if (!pts.length) return;
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      pts.forEach((p, i) => {
        const px = X(p[0]), py = Y(p[1] / div);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      ctx.stroke();
    };
    line(dis, "#2c4a7c");
    line(ch, "#c0392b");

    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillStyle = "#c0392b"; ctx.fillText("充电", pad.l + 6, pad.t + 12);
    ctx.fillStyle = "#2c4a7c"; ctx.fillText("放电", pad.l + 46, pad.t + 12);
  }

  function fitCanvas(cv, height) {
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth || 640;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(height * dpr);
    cv.style.height = height + "px";
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, height);
    return { ctx: ctx, w: w, h: height };
  }

  function axisFrame(ctx, pad, w, h, yLabel) {
    ctx.strokeStyle = "#e4e7ec";
    ctx.lineWidth = 1;
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.fillStyle = "#98a2b3";
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (h - pad.t - pad.b) * i / 4;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    }
    if (yLabel) {
      ctx.save();
      ctx.translate(12, pad.t + (h - pad.t - pad.b) / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText(yLabel, 0, 0);
      ctx.restore();
    }
  }

  function drawCycleChart(cv, rows, mass) {
    if (!cv || !rows.length) return;
    const div = mass > 0 ? mass / 1000 : 1;
    const { ctx, w, h } = fitCanvas(cv, 250);
    const pad = { l: 56, r: 66, t: 18, b: 30 };
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    const xs = rows.map((r) => r.cycle);
    const x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    const yMax = Math.max.apply(null, rows.map((r) => Math.max(r.discharge_mAh, r.charge_mAh) / div)) * 1.1 || 1;
    const rets = rows.map((r) => r.retention_pct).filter((v) => v != null);
    const rMin = Math.min(80, Math.min.apply(null, rets.concat([100])) - 5);
    const X = (v) => pad.l + (x1 === x0 ? iw / 2 : (v - x0) / (x1 - x0) * iw);
    const Y = (v) => pad.t + ih - (v / yMax) * ih;
    const YR = (v) => pad.t + ih - ((v - rMin) / Math.max(1, 105 - rMin)) * ih;

    axisFrame(ctx, pad, w, h, mass > 0 ? "比容量 mAh/g" : "容量 mAh");
    ctx.textAlign = "right";
    ctx.fillStyle = "#98a2b3";
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + ih * i / 4;
      ctx.fillText((yMax * (4 - i) / 4).toFixed(0), pad.l - 8, y + 4);
      ctx.textAlign = "left";
      ctx.fillText((105 - (105 - rMin) * i / 4).toFixed(0) + "%", w - pad.r + 8, y + 4);
      ctx.textAlign = "right";
    }
    ctx.textAlign = "center";
    ctx.fillStyle = "#98a2b3";
    [x0, Math.round((x0 + x1) / 2), x1].forEach((v) => ctx.fillText(String(v), X(v), h - 10));

    const line = (getY, key, color, dash) => {
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.setLineDash(dash || []);
      rows.forEach((r, i) => {
        const val = key === "retention_pct" ? r[key] : r[key] / div;
        const px = X(r.cycle), py = getY(val);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    };
    line(Y, "discharge_mAh", "#2c4a7c");
    line(Y, "charge_mAh", "#9db3d4");
    if (rets.length) line(YR, "retention_pct", "#b45309", [4, 3]);

    ctx.font = "11px system-ui";
    ctx.textAlign = "left";
    ctx.fillStyle = "#2c4a7c"; ctx.fillText("■ 放电", pad.l, 11);
    ctx.fillStyle = "#9db3d4"; ctx.fillText("■ 充电", pad.l + 56, 11);
    ctx.fillStyle = "#b45309"; ctx.fillText("■ 保持率", pad.l + 112, 11);
  }

  function drawCurveChart(cv, pts, cycle, mass) {
    if (!cv || !pts.length) return;
    const div = mass > 0 ? mass / 1000 : 1;   // mg -> g
    const { ctx, w, h } = fitCanvas(cv, 230);
    const pad = { l: 46, r: 26, t: 16, b: 30 };
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    // 放电为负、充电为正，横轴统一用绝对容量，曲线呈 V 形
    const qs = pts.map((p) => Math.abs(p.q) / div);
    const vs = pts.map((p) => p.v);
    const q0 = Math.min.apply(null, qs), q1 = Math.max.apply(null, qs);
    const v0 = Math.min.apply(null, vs), v1 = Math.max.apply(null, vs);
    const X = (v) => pad.l + (q1 === q0 ? iw / 2 : (v - q0) / (q1 - q0) * iw);
    const Y = (v) => pad.t + ih - (v1 === v0 ? ih / 2 : (v - v0) / (v1 - v0) * ih);

    axisFrame(ctx, pad, w, h, "电压 V");
    ctx.fillStyle = "#98a2b3";
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + ih * i / 4;
      ctx.fillText((v1 - (v1 - v0) * i / 4).toFixed(2), pad.l - 8, y + 4);
    }
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) {
      const x = pad.l + iw * i / 4;
      ctx.fillText((q0 + (q1 - q0) * i / 4).toFixed(0), x, h - 10);
    }

    ctx.beginPath();
    ctx.strokeStyle = "#2c4a7c";
    ctx.lineWidth = 1.8;
    pts.forEach((p, i) => {
      const px = X(Math.abs(p.q) / div), py = Y(p.v);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    ctx.stroke();

    ctx.fillStyle = "#98a2b3";
    ctx.textAlign = "left";
    ctx.fillText("第 " + cycle + " 圈", pad.l, 11);
    ctx.textAlign = "right";
    ctx.fillText("容量 mAh", w - pad.r, h - 10);
  }

  async function uploadNdax(file) {
    const fd = new FormData();
    fd.append("file", file);
    toast("正在上传并解析 " + file.name + "…");
    try {
      const res = await fetch("/api/tests/upload", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "上传失败");
      TS.loaded = false;
      await loadTests(true);
      await openTest(data.id);
      toast("解析完成：" + (data.cycles || 0) + " 圈 · 通道 " + (data.channel_code || "未匹配"), "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  function bindTestsUI() {
    const btn = $("#tsUpload"), input = $("#tsFile");
    if (!btn || !input) return;
    btn.onclick = () => input.click();
    input.onchange = () => {
      if (input.files.length) uploadNdax(input.files[0]);
      input.value = "";
    };
    window.addEventListener("resize", () => {
      if (!TS.current) return;
      const cyc = (TS.current.cycle_rows || []).filter((c) => (c.discharge_mAh || 0) > 0);
      drawCycleChart($("#tsCycleChart"), cyc);
    });
  }

  /* ---------------------------------------------------------- 账号 */

  let ME = null;

  async function loadMe() {
    ME = await api("/api/me");
    renderUser();
  }

  function renderUser() {
    if (!ME) return;
    const u = ME.user;
    const name = u.display || u.username;
    $("#uName").textContent = name;
    $("#uAvatar").textContent = name.slice(0, 1).toUpperCase();
    $("#uDisplay").textContent = name;
    $("#uRole").textContent = ME.is_admin ? "管理员" : (u.beta ? "内测用户" : "成员");
    // 报账入口只给管理员和内测用户看
    const expTab = $("#tabExpenses");
    if (expTab) {
      const can = !!(ME.is_admin || u.beta);
      expTab.hidden = !can;
      if (!can && S.tab === "expenses") switchTab("board");
    }
    document.querySelectorAll(".admin-only").forEach((b) => {
      b.classList.toggle("hidden", !ME.is_admin);
    });
    const acting = ME.acting_as;
    $("#actingBar").classList.toggle("hidden", !acting);
    if (acting) $("#actingName").textContent = acting.display || acting.username;
  }

  function toggleMenu(force) {
    const m = $("#umenu");
    const show = force === undefined ? m.classList.contains("hidden") : force;
    m.classList.toggle("hidden", !show);
  }

  async function doLogout() {
    // 用 GET 而不是 POST：实测同样的请求 POST 时浏览器没带上会话 Cookie，
    // 服务端收不到 token 就删不掉 session，表现就是「点了退出还停在里面」。
    try { await fetch("/api/logout", { cache: "no-store", credentials: "same-origin" }); } catch (e) { /* 忽略 */ }
    // 带一个变化的参数，避免浏览器把「跳到自己」当成无变化而不重新请求
    location.replace("/?out=" + Date.now());
  }

  async function exitActing() {
    try {
      await api("/api/admin/act-as", { method: "POST", body: { user_id: null } });
      await new Promise((res) => setTimeout(res, 120));
      await loadMe();
      TS.loaded = false;
      await loadState();
      await loadTests(true);
      toast("已退出查看，回到自己的数据", "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  function openPasswordModal() {
    openModal({
      title: "修改密码",
      body:
        '<div class="form-row"><label>原密码</label><input id="pwOld" type="password" autocomplete="current-password"></div>' +
        '<div class="form-row"><label>新密码（至少 6 位）</label><input id="pwNew" type="password" autocomplete="new-password"></div>' +
        '<div class="form-row"><label>再输一次</label><input id="pwNew2" type="password" autocomplete="new-password"></div>',
      foot: '<button class="btn ghost" data-close>取消</button><button class="btn" id="pwSave">保存</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#pwSave").onclick = async () => {
          const o = $("#pwOld").value, n = $("#pwNew").value, n2 = $("#pwNew2").value;
          if (n.length < 6) return toast("新密码至少 6 位", "err");
          if (n !== n2) return toast("两次输入的新密码不一致", "err");
          try {
            await api("/api/me/password", { method: "POST", body: { old: o, new: n } });
            closeModal();
            toast("密码已更新");
            setTimeout(() => location.reload(), 900);
          } catch (e) { toast(e.message, "err"); }
        };
      },
    });
  }

  async function openActAsModal() {
    let users;
    try { users = (await api("/api/admin/users")).users; } catch (e) { return toast(e.message, "err"); }
    const rows = users.map((u) =>
      '<div class="user-row">' +
        '<div class="who"><b>' + esc(u.display || u.username) + '</b>' +
        '<span>@' + esc(u.username) + ' · ' + (u.db_exists ? "有数据" : "暂无数据") + '</span></div>' +
        '<button class="btn tiny" data-pick="' + u.id + '">查看</button>' +
      '</div>').join("");
    openModal({
      title: "查看他人数据",
      body: rows || "<p>还没有其他用户</p>",
      foot: '<button class="btn ghost" data-close>关闭</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#modalBody").querySelectorAll("[data-pick]").forEach((b) => {
          b.onclick = async () => {
            try {
              const r = await api("/api/admin/act-as", { method: "POST", body: { user_id: Number(b.dataset.pick) } });
              closeModal();
              // 不整页跳转，直接把状态重新拉一遍：跳转会用到缓存，容易看起来「点了没反应」
              await new Promise((res) => setTimeout(res, 120));
              await loadMe();
              TS.loaded = false;
              await loadState();
              await loadTests(true);
              toast("正在查看 " + (r.name || "该用户") + " 的数据", "ok");
            } catch (e) { toast(e.message, "err"); }
          };
        });
      },
    });
  }

  async function openUsersModal() {
    let data;
    try { data = await api("/api/admin/users"); } catch (e) { return toast(e.message, "err"); }

    const rows = data.users.map((u) => {
      const bits = [
        '<div class="who"><b>' + esc(u.display || u.username) + '</b>',
        '<span>@' + esc(u.username) +
          (u.last_login ? " · 最近 " + esc(u.last_login.replace("T", " ").slice(5, 16)) : " · 从未登录") +
          "</span></div>",
        u.role === "admin" ? '<span class="tag admin">管理员</span>' : "",
        u.beta ? '<span class="tag">内测</span>' : "",
        u.active ? "" : '<span class="tag off">已停用</span>',
      ];
      if (u.id !== data.me) {
        bits.push('<button class="btn tiny ghost" data-pw="' + u.id + '">重置密码</button>');
        bits.push('<button class="btn tiny ghost" data-toggle="' + u.id + '" data-active="' + (u.active ? 1 : 0) + '">' +
          (u.active ? "停用" : "启用") + "</button>");
        if (u.role !== "admin") {
          bits.push('<button class="btn tiny ghost" data-beta="' + u.id + '" data-on="' + (u.beta ? 1 : 0) + '">' +
            (u.beta ? "取消内测" : "设为内测") + "</button>");
        }
      }
      return '<div class="user-row">' + bits.join("") + "</div>";
    }).join("");

    openModal({
      title: "用户管理",
      wide: true,
      body:
        '<div style="margin-bottom:14px;padding:13px;border:1px dashed var(--line);border-radius:10px">' +
          '<div style="font-size:12.5px;color:var(--muted);margin-bottom:9px">新建账号</div>' +
          '<div class="form-row"><label>用户名（登录用）</label><input id="nuName" placeholder="例如 zhangsan"></div>' +
          '<div class="form-row"><label>显示名称（可选）</label><input id="nuDisplay" placeholder="例如 张三"></div>' +
          '<div class="form-row"><label>初始密码（至少 6 位）</label><input id="nuPass" placeholder="告诉对方后请他自行修改"></div>' +
          '<button class="btn" id="nuSave">创建账号</button>' +
        "</div>" + rows,
      foot: '<button class="btn ghost" data-close>关闭</button>',
      onOpen: () => {
        $("#modalFoot [data-close]").onclick = closeModal;
        $("#nuSave").onclick = async () => {
          try {
            await api("/api/admin/users", { method: "POST", body: {
              username: $("#nuName").value.trim(),
              display: $("#nuDisplay").value.trim(),
              password: $("#nuPass").value,
            }});
            toast("账号已创建");
            openUsersModal();
          } catch (e) { toast(e.message, "err"); }
        };
        $("#modalBody").querySelectorAll("[data-pw]").forEach((b) => {
          b.onclick = async () => {
            const pw = prompt("给这个账号设置新密码（至少 6 位）");
            if (!pw) return;
            try {
              await api("/api/admin/users/" + b.dataset.pw + "/password", { method: "POST", body: { password: pw } });
              toast("密码已重置");
            } catch (e) { toast(e.message, "err"); }
          };
        });
        $("#modalBody").querySelectorAll("[data-beta]").forEach((b) => {
      b.onclick = async () => {
        const on = b.dataset.on === "1";
        try {
          await api("/api/admin/users/" + b.dataset.beta + "/beta", { method: "POST", body: { beta: !on } });
          closeModal();
          openUsersModal();
          toast(!on ? "已设为内测用户" : "已取消内测");
        } catch (e) { toast(e.message, "err"); }
      };
    });

    $("#modalBody").querySelectorAll("[data-toggle]").forEach((b) => {
          b.onclick = async () => {
            try {
              await api("/api/admin/users/" + b.dataset.toggle + "/active", {
                method: "POST", body: { active: b.dataset.active !== "1" } });
              openUsersModal();
            } catch (e) { toast(e.message, "err"); }
          };
        });
      },
    });
  }

  function bindUserUI() {
    $("#btnUser").onclick = (e) => { e.stopPropagation(); toggleMenu(); };
    $("#umenu").onclick = (e) => {
      const act = e.target.dataset ? e.target.dataset.act : null;
      if (!act) return;
      toggleMenu(false);
      if (act === "password") openPasswordModal();
      else if (act === "actas") openActAsModal();
      else if (act === "users") openUsersModal();
      else if (act === "logout") doLogout();
    };
    document.addEventListener("click", (e) => {
      if (!$("#ubox").contains(e.target)) toggleMenu(false);
    });
    $("#btnExitActing").onclick = exitActing;
  }

  /* ---------------------------------------------------------- 启动 */

  async function boot() {
    bind();
    bindUserUI();
    bindMethodsUI();
    bindTestsUI();
    initDensity();
    // 四个首屏请求一起发：之前是串行，等于白等三个网络来回
    const boot4 = await Promise.allSettled([loadMe(), loadTests(), loadState(), loadRecent()]);
    if (boot4[0].status === "rejected") {
      // 会话过期或未登录，回登录页
      location.replace("/?expired=" + Date.now());
      return;
    }
    const h = (location.hash || "").replace("#", "");
    if (h && TABS.indexOf(h) >= 0) switchTab(h);
  }

  window.addEventListener("hashchange", () => {
    const h = (location.hash || "").replace("#", "");
    if (h && h !== S.tab && TABS.indexOf(h) >= 0) switchTab(h);
  });

  document.addEventListener("DOMContentLoaded", boot);
})();
