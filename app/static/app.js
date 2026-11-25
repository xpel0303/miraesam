// 모바일 메뉴
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-menu-toggle]");
  if (t) {
    const shell = document.querySelector(".shell");
    const open = shell.classList.toggle("open");
    t.setAttribute("aria-expanded", open ? "true" : "false");
  }
  const c = e.target.closest("[data-copy]");
  if (c) {
    navigator.clipboard.writeText(c.dataset.copy).then(() => {
      const old = c.textContent;
      c.textContent = "복사했어요";
      setTimeout(() => (c.textContent = old), 1500);
    });
  }
});

// 작업 진행 표시: <div class="job" data-job-id="12"></div>
function watchJob(el) {
  const id = el.dataset.jobId;
  el.innerHTML = '<div class="small"><b data-title>작업</b> <span class="muted" data-step>확인하는 중</span></div><div class="bar"><i></i></div><div data-out class="small"></div>';
  const title = el.querySelector("[data-title]");
  const step = el.querySelector("[data-step]");
  const bar = el.querySelector(".bar > i");
  const out = el.querySelector("[data-out]");
  let delay = 1500;

  async function tick() {
    let j;
    try {
      const r = await fetch(`/api/jobs/${id}`, { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      j = await r.json();
    } catch (err) {
      step.textContent = "상태를 불러오지 못했어요. 잠시 뒤 다시 확인합니다.";
      setTimeout(tick, 5000);
      return;
    }
    title.textContent = j.title || "작업";
    step.textContent = (j.status === "queued" ? (j.step || "차례를 기다리는 중") + (j.ahead ? ` · 앞에 작업 ${j.ahead}개` : "") : j.step) + etaText(j);
    bar.style.width = (j.status === "done" ? 100 : Math.max(j.progress, 4)) + "%";
    if (j.status === "done") {
      el.classList.add("done");
      const r = j.result || {};
      if (r.files && r.files.length) {
        out.innerHTML = r.files.map((f) => `<a class="btn small" style="margin-right:6px" href="/files/${f.file_id}" target="_blank" rel="noopener">${escapeHtml(f.filename || "파일")} 열기</a>`).join("");
      } else if (r.file_id) {
        out.innerHTML = `<a class="btn small" href="/files/${r.file_id}" target="_blank" rel="noopener">${escapeHtml(r.filename || "파일 열기")} 열기</a>`;
      } else if (r.reply) {
        out.innerHTML = `<p style="margin:6px 0 0">${escapeHtml(r.reply)}</p><span class="muted">모델: ${escapeHtml(r.model || "")}</span>`;
      }
      if (r.note) out.insertAdjacentHTML("beforeend", `<p class="small" style="margin:6px 0 0;color:var(--warn, #b26b00)">${escapeHtml(r.note)}</p>`);
      el.dispatchEvent(new CustomEvent("job:done", { detail: j, bubbles: true }));
      return;
    }
    if (j.status === "failed") {
      el.classList.add("failed");
      out.innerHTML = `<p style="margin:6px 0 0;color:var(--err)">${escapeHtml(j.error || "작업이 실패했습니다.")}</p>`;
      return;
    }
    delay = Math.min(delay + 500, 4000);
    setTimeout(tick, delay);
  }
  tick();
}

// 예상 남은 시간: "· 약 3분 남음" / 아직 모르면 비움
function etaText(j) {
  if (j.status !== "running" && j.status !== "queued") return "";
  if (j.eta == null) return j.status === "running" ? " · 남은 시간 계산 중" : "";
  const s = j.eta;
  const t = s < 60 ? "1분 안쪽" : s < 3600 ? `약 ${Math.round(s / 60)}분` : `약 ${Math.floor(s / 3600)}시간 ${Math.round((s % 3600) / 60)}분`;
  return j.status === "running" ? ` · ${t} 남음` : ` · 보통 ${t} 걸려요`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".job[data-job-id]").forEach(watchJob);

// ---------------------------------------------------------------- PDF·사진에서 지문 가져오기
// 지문 넣는 칸(textarea name=p_text / passage) 위에 버튼을 붙임. 지문 카드를 복제해도 같이 따라감.
(function () {
  const SEL = 'textarea[name="p_text"], textarea[name="passage"], textarea#l-text';
  function attach(root) {
    (root.querySelectorAll ? root : document).querySelectorAll(SEL).forEach((ta) => {
      const prev = ta.previousElementSibling;
      if (prev && prev.classList && prev.classList.contains("ocr-bar")) return;
      ta.insertAdjacentHTML("beforebegin",
        '<div class="ocr-bar" style="display:flex;gap:8px;align-items:center;margin:0 0 4px">' +
        '<button type="button" class="btn ghost small ocr-btn" style="padding:3px 10px;font-size:12.5px">📄 PDF·사진에서 지문 가져오기</button>' +
        '<button type="button" class="btn ghost small cur-btn" style="padding:3px 10px;font-size:12.5px" title="이 지문이 어떤 사회·과학 교과 단원과 이어지는지 찾아요">📚 교과 연계 찾기</button>' +
        '<span class="small muted ocr-msg">또는 PDF·사진을 아래 칸에 끌어다 놓거나 붙여넣기(Ctrl+V)</span></div>');
    });
  }
  async function runOcr(files, bar) {
      const btn = bar.querySelector(".ocr-btn"), ta = bar.nextElementSibling, msg = bar.querySelector(".ocr-msg");
      files = Array.from(files || []).filter((f) => /pdf|image\//.test(f.type) || /\.(pdf|png|jpe?g|heic|webp)$/i.test(f.name || ""));
      if (!files.length) return false;
      if (btn.disabled) return true;
      const fd = new FormData();
      for (const f of files) fd.append("files", f, f.name || "붙여넣은그림.png");
      btn.disabled = true; msg.style.color = ""; msg.textContent = "글자로 바꾸는 중… (쪽당 10~20초)";
      try {
        const r = await fetch("/api/ocr", { method: "POST", body: fd });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.text) throw new Error(j.error || "가져오지 못했어요.");
        const parts = j.passages && j.passages.length ? j.passages : [j.text];
        const nums = j.numbers || [];
        const setTitle = (t, i) => {  // 번호가 있으면 그 지문 카드의 제목 칸이 비었을 때 '18번' 으로
          const card = t.closest(".passage-card, section, .panel"); const ti = card && card.querySelector('input[name="p_title"]');
          if (ti && !ti.value.trim() && nums[i]) ti.value = nums[i] + "번";
        };
        if (ta.id === "l-text") {  // 지문 보관함: 번호 줄째 넣으면 저장할 때 번호마다 나눠 정렬됨
          ta.value = (ta.value.trim() ? ta.value.trim() + "\n\n" : "") + (j.numbered || parts.join("\n===\n"));
          msg.textContent = `지문 ${parts.length}개를 넣었어요. 확인한 뒤 '보관함에 저장'을 눌러 주세요.`;
          ta.dispatchEvent(new Event("input", { bubbles: true }));
          return;
        }
        // 지문 여러 개 + '지문 추가' 버튼이 있으면 칸을 늘려 하나씩 넣음
        const add = document.getElementById("add-passage");
        if (parts.length > 1 && add && ta.name === "p_text") {
          let cur = ta;
          ta.value = (ta.value.trim() ? ta.value.trim() + "\n\n" : "") + parts[0];
          setTitle(ta, 0);
          let pi = 0;
          for (const t of parts.slice(1)) {
            pi++;
            const all = () => Array.from(document.querySelectorAll('textarea[name="p_text"]'));
            let next = all()[all().indexOf(cur) + 1];
            if (!next || next.value.trim()) { const n = all().length; add.click(); attach(document); next = all()[n] || all()[all().length - 1]; }
            if (!next || next === cur) { cur.value += "\n\n" + t; continue; }
            next.value = t; setTitle(next, pi); next.dispatchEvent(new Event("input", { bubbles: true })); cur = next;
          }
          msg.textContent = `지문 ${parts.length}개를 칸마다 넣었어요. 확인해 주세요.`;
        } else {
          if (parts.length === 1) setTitle(ta, 0);
          ta.value = (ta.value.trim() ? ta.value.trim() + "\n\n" : "") + parts.join("\n\n");
          msg.textContent = parts.length > 1 ? `지문 ${parts.length}개를 넣었어요. 확인해 주세요.` : "넣었어요. 틀린 글자가 없는지 확인해 주세요.";
        }
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      } catch (err) {
        msg.style.color = "var(--err)"; msg.textContent = err.message;
      } finally {
        btn.disabled = false; ta.classList.remove("ocr-drop");
        document.querySelectorAll(".ocr-bar").forEach((b) => { if (b !== bar) { b.querySelector(".ocr-btn").disabled = false; b.querySelector(".ocr-msg").textContent = ""; } });
      }
      return true;
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".ocr-btn");
    if (!btn) return;
    const bar = btn.closest(".ocr-bar");
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = "application/pdf,image/*";
    inp.onchange = () => runOcr(inp.files, bar);
    inp.click();
  });
  // 지문 칸에 PDF·사진을 바로 끌어다 놓거나 붙여넣기(Ctrl+V) 해도 글자로 바꿔 넣음
  const barOf = (ta) => (ta.previousElementSibling && ta.previousElementSibling.classList.contains("ocr-bar") ? ta.previousElementSibling : null);
  const hasFiles = (dt) => dt && Array.from(dt.types || []).includes("Files");
  document.addEventListener("dragover", (e) => {
    const ta = e.target.closest && e.target.closest(SEL);
    if (ta && hasFiles(e.dataTransfer)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; ta.classList.add("ocr-drop"); }
  });
  document.addEventListener("dragleave", (e) => { const ta = e.target.closest && e.target.closest(SEL); if (ta) ta.classList.remove("ocr-drop"); });
  document.addEventListener("drop", (e) => {
    const ta = e.target.closest && e.target.closest(SEL);
    if (!ta || !hasFiles(e.dataTransfer)) return;
    e.preventDefault(); ta.classList.remove("ocr-drop");
    const bar = barOf(ta); if (bar) runOcr(e.dataTransfer.files, bar);
  });
  document.addEventListener("paste", (e) => {
    const ta = e.target.closest && e.target.closest(SEL);
    if (!ta || !e.clipboardData) return;
    const files = Array.from(e.clipboardData.files || []);
    if (!files.length) return;  // 글자 붙여넣기는 그대로
    e.preventDefault();
    const bar = barOf(ta); if (bar) runOcr(files, bar);
  });
  attach(document);
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && attach(n)))).observe(document.body, { childList: true, subtree: true });
})();

// ---------------------------------------------------------------- 목표어법 칸: 교과서에서 고르기
(function () {
  let books = null;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function attach(root) {
    (root.querySelectorAll ? root : document).querySelectorAll('input[name="p_grammar"], input[name="common_grammar"], input[name="p_target"], form[action="/grammar"] input[name="target"]').forEach((inp) => {
      const nx = inp.nextElementSibling;
      if (nx && nx.classList && nx.classList.contains("gb-btn")) return;
      inp.insertAdjacentHTML("afterend", '<button type="button" class="btn ghost small gb-btn" style="margin-top:4px;padding:3px 10px;font-size:12.5px">📚 교과서에서 고르기</button>');
    });
  }
  let dlg, target;
  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "picker";
    dlg.innerHTML = `<form method="dialog" class="picker-inner">
      <div class="picker-head"><h2>교과서 목표어법</h2><button class="linklike" value="cancel">닫기 ✕</button></div>
      <div class="picker-filters"><select data-book aria-label="교과서"></select></div>
      <div class="picker-list" data-list></div>
      <div class="picker-foot"><a class="small" href="/library/grammar" target="_blank" rel="noopener">목록 고치기</a>
        <span class="small muted" data-count></span><button class="btn" value="ok" data-ok>넣기</button></div></form>`;
    document.body.appendChild(dlg);
    const sel = dlg.querySelector("[data-book]"), list = dlg.querySelector("[data-list]"), cnt = dlg.querySelector("[data-count]");
    const draw = () => {
      const b = books.find((x) => String(x.id) === sel.value);
      try { localStorage.setItem("gb-last", sel.value); } catch (e) {}
      const ls = (b && b.lessons) || [];
      list.innerHTML = ls.length ? ls.map((l) => `<div style="padding:6px 2px;border-bottom:1px solid var(--line)"><b class="small">${esc(l.name)}</b>
        ${(l.points || []).length ? `<label class="small" style="font-weight:400;margin-left:8px"><input type="checkbox" data-all-lesson> 이 과 전체</label>` : ""}<div style="display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:3px">
        ${(l.points || []).map((p) => `<label class="small" style="font-weight:400;margin:0"><input type="checkbox" name="gp" value="${esc(p)}"> ${esc(p)}</label>`).join("") || '<span class="small muted">문법이 아직 없어요</span>'}</div></div>`).join("")
        : `<p class="small muted">이 교과서에는 아직 과별 목표어법이 없어요. <a href="/library/grammar?open=${b ? b.id : ""}#b${b ? b.id : ""}" target="_blank" rel="noopener">여기서 적어 두세요</a>.</p>`;
      sync();
    };
    const sync = () => { cnt.textContent = `고른 문법 ${dlg.querySelectorAll("[name=gp]:checked").length}개`; };
    list.addEventListener("change", (e) => {
      if (e.target.matches("[data-all-lesson]")) e.target.closest("div").querySelectorAll("[name=gp]").forEach((x) => (x.checked = e.target.checked));
      sync();
    });
    sel.addEventListener("change", draw);
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok" || !target) return;
      const picked = [...dlg.querySelectorAll("[name=gp]:checked")].map((x) => x.value);
      if (!picked.length) return;
      const have = target.value.split("/").map((x) => x.trim()).filter(Boolean);
      picked.forEach((p) => { if (!have.includes(p)) have.push(p); });
      target.value = have.join(" / ");
      target.dispatchEvent(new Event("input", { bubbles: true }));
    });
    dlg._draw = draw; dlg._sel = sel;
  }
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".gb-btn");
    if (!btn) return;
    target = btn.previousElementSibling;
    if (!books) {
      const r = await fetch("/api/grammar-books");
      if (!r.ok) { alert("교과서 목록을 불러오지 못했어요."); return; }
      books = (await r.json()).books;
    }
    if (!dlg) build();
    dlg._sel.innerHTML = books.map((b) => `<option value="${b.id}">${esc(b.subject)} · ${esc(b.name)}${(b.lessons || []).some((l) => (l.points || []).length) ? "" : " (비어 있음)"}</option>`).join("");
    let last = null; try { last = localStorage.getItem("gb-last"); } catch (e2) {}
    if (last && books.some((b) => String(b.id) === last)) dlg._sel.value = last;
    else { const f = books.find((b) => (b.lessons || []).some((l) => (l.points || []).length)); if (f) dlg._sel.value = String(f.id); }
    dlg._draw();
    dlg.showModal();
  });
  attach(document);
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && attach(n)))).observe(document.body, { childList: true, subtree: true });
})();


// ---------------------------------------------------------------- 파일 올리는 칸: 끌어다 놓기·붙여넣기(Ctrl+V)
// 시험지 올리기 등 input[type=file] 을 넓은 칸으로 감싸서, 파일을 끌어다 놓거나 캡처한 그림을 붙여넣어도 들어가게.
(function () {
  const inputs = Array.from(document.querySelectorAll('input[type="file"]')).filter((i) => !i.hidden && getComputedStyle(i).display !== "none" && !i.closest(".no-drop, [hidden]"));
  if (!inputs.length) return;
  const accepts = (inp, f) => {
    const acc = (inp.accept || "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
    if (!acc.length) return true;
    const n = (f.name || "").toLowerCase(), t = (f.type || "").toLowerCase();
    return acc.some((a) => (a.startsWith(".") ? n.endsWith(a) : a.endsWith("/*") ? t.startsWith(a.slice(0, -1)) : t === a));
  };
  const put = (inp, files, add) => {
    files = Array.from(files).filter((f) => accepts(inp, f));
    if (!files.length) return false;
    const dt = new DataTransfer();
    if (add && inp.multiple) Array.from(inp.files || []).forEach((f) => dt.items.add(f));
    (inp.multiple ? files : files.slice(0, 1)).forEach((f, k) => dt.items.add(f.name ? f : new File([f], `붙여넣은그림-${Date.now()}-${k}.png`, { type: f.type || "image/png" })));
    inp.files = dt.files;
    inp.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };
  inputs.forEach((inp) => {
    const zone = document.createElement("div");
    zone.className = "drop-zone";
    inp.parentNode.insertBefore(zone, inp);
    zone.appendChild(inp);
    zone.insertAdjacentHTML("beforeend", '<div class="dz-hint">📥 파일을 여기로 끌어다 놓거나, 캡처한 그림을 <b>Ctrl+V</b>로 붙여넣어도 돼요</div>');
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("on"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("on"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("on"); put(inp, e.dataTransfer.files, true); });
  });
  // 붙여넣기: 글 쓰는 칸이 아닌 곳에서 Ctrl+V 하면 화면의 (첫) 파일 칸에 추가
  document.addEventListener("paste", (e) => {
    if (e.defaultPrevented || !e.clipboardData) return;
    const t = e.target;
    if (t && (t.closest("textarea") || (t.closest("input") && t.type !== "file") || t.isContentEditable)) return;
    const files = Array.from(e.clipboardData.files || []);
    if (!files.length) return;
    const inp = inputs.find((i) => files.some((f) => accepts(i, f)));
    if (inp && put(inp, files, true)) { e.preventDefault(); inp.closest(".drop-zone").scrollIntoView({ block: "center", behavior: "smooth" }); }
  });
})();


// ---------------------------------------------------------------- 교과 연계 찾기 (사회·과학, 필요할 때만)
// 지문 칸 위 '📚 교과 연계 찾기' → 어느 과목·학년·단원과 이어지는지 칸 아래에 보여 주고, 제목에 붙일 수 있게.
(function () {
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".cur-btn");
    if (btn) {
      const bar = btn.closest(".ocr-bar"), ta = bar.nextElementSibling;
      let box = ta.nextElementSibling;
      if (!box || !box.classList.contains("cur-box")) { ta.insertAdjacentHTML("afterend", '<div class="cur-box"></div>'); box = ta.nextElementSibling; }
      if (ta.value.trim().split(/\s+/).length < 15) { box.innerHTML = '<span class="small" style="color:var(--err)">지문을 먼저 넣어 주세요.</span>'; return; }
      btn.disabled = true; box.innerHTML = '<span class="small muted">교과 단원 찾는 중… (10초쯤)</span>';
      try {
        const r = await fetch("/api/curriculum", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: ta.value }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || "찾지 못했어요.");
        if (!j.links || !j.links.length) { box.innerHTML = '<span class="small muted">이어지는 사회·과학 단원이 없어요.' + (j.topic ? ` (주제: ${esc(j.topic)})` : "") + "</span>"; return; }
        const card = ta.closest(".passage-card, section, .panel, form");
        const title = card && card.querySelector('input[name="p_title"], input[name="title"]');
        box.innerHTML = (j.topic ? `<div class="small muted" style="margin-bottom:4px">지문 주제: ${esc(j.topic)}</div>` : "") + j.links.map((l, k) => `
          <div class="cur-item ${l.fit === "높음" ? "hi" : ""}">
            <div><b>${esc(l.subject)}</b> <span class="cur-g">${esc(l.grade)}</span> · ${esc(l.unit)} <span class="cur-a">${esc(l.area)}</span> <span class="cur-fit">${esc(l.fit)}</span></div>
            <div class="small">${esc(l.reason)}${l.terms && l.terms.length ? ` <span class="muted">· ${l.terms.map(esc).join(", ")}</span>` : ""}</div>
            <div class="small muted">성취기준 ${esc(l.std)}${title ? ` · <a href="#" class="cur-tag" data-tag="${esc(l.tag)}">제목에 [${esc(l.tag)}] 붙이기</a>` : ""}</div>
          </div>`).join("");
      } catch (err) {
        box.innerHTML = `<span class="small" style="color:var(--err)">${esc(err.message)}</span>`;
      } finally { btn.disabled = false; }
      return;
    }
    const tag = e.target.closest(".cur-tag");
    if (tag) {
      e.preventDefault();
      const box = tag.closest(".cur-box"), ta = box.previousElementSibling;
      const card = ta.closest(".passage-card, section, .panel, form");
      const title = card && card.querySelector('input[name="p_title"], input[name="title"]');
      if (!title) return;
      const t = `[${tag.dataset.tag}]`;
      if (!title.value.includes(t)) title.value = (title.value.trim() + " " + t).trim();
      tag.textContent = "붙였어요 ✓";
    }
  });
})();
