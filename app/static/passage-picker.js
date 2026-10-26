/* 지문 보관함에서 불러오기
   자료 만들기 화면(지문 칸이 있는 곳)에 '📚 저장된 지문 불러오기' 버튼을 붙이고, 고른 지문을 빈 지문 칸에 차례로 넣습니다.
   - 여러 지문 화면: #passages 안의 .passage-card (모자라면 '지문 추가'를 눌러 칸을 늘림)
   - 지문 하나 화면(단어장): textarea#passage */
(() => {
  const box = document.getElementById("passages");
  const single = box ? null : document.querySelector("textarea#passage");
  if (!box && !single) return;

  const bar = document.createElement("div");
  bar.className = "picker-bar";
  bar.innerHTML = `<button type="button" class="btn ghost">📚 저장된 지문 불러오기</button>
    <span class="small muted">지문 보관함에서 골라 넣어요. 새로 넣은 지문은 자료를 만들면 보관함에 자동 저장돼요.</span>`;
  (box || single.closest(".field") || single).before(bar);

  const dlg = document.createElement("dialog");
  dlg.className = "picker";
  dlg.innerHTML = `
    <form method="dialog" class="picker-inner">
      <div class="picker-head"><h2>저장된 지문 불러오기</h2><button class="linklike" value="cancel" aria-label="닫기">닫기 ✕</button></div>
      <div class="picker-filters">
        <input type="search" placeholder="제목·본문·출처 검색" aria-label="검색" data-q>
        <select aria-label="분류" data-group><option value="">전체 분류</option></select>
      </div>
      <div class="picker-list" data-list><p class="muted small">불러오는 중…</p></div>
      <div class="picker-foot">
        <span class="small muted" data-count>고른 지문 0개</span>
        <a class="small" href="/library" target="_blank" rel="noopener">보관함 열기</a>
        <button class="btn" value="ok" data-ok disabled>넣기</button>
      </div>
    </form>`;
  document.body.appendChild(dlg);
  const $ = (s) => dlg.querySelector(s);
  const list = $("[data-list]"), q = $("[data-q]"), grp = $("[data-group]"), ok = $("[data-ok]"), count = $("[data-count]");
  let items = [], picked = [], timer = null, groupsLoaded = false;
  const cache = {};

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function draw() {
    if (!items.length) { list.innerHTML = `<p class="muted small">${q.value || grp.value ? "찾는 지문이 없어요." : "아직 저장된 지문이 없어요. 자료를 만들면 넣은 지문이 자동으로 저장돼요."}</p>`; return; }
    list.innerHTML = items.map((p) => `
      <label class="picker-item">
        <input type="${single ? "radio" : "checkbox"}" name="pick" value="${p.id}" ${picked.includes(p.id) ? "checked" : ""}>
        <span><em class="pi-head"><b>${esc(p.title)}</b>${p.group ? ` <i class="tag">${esc(p.group)}</i>` : ""}${p.source ? ` <small class="muted">${esc(p.source)}</small>` : ""}</em>
          <small class="picker-preview">${esc(p.text.slice(0, 180))}${p.text.length > 180 ? "…" : ""}</small>
          <small class="muted">${p.words}단어</small></span>
      </label>`).join("");
  }
  async function load() {
    const res = await fetch(`/api/passages?q=${encodeURIComponent(q.value)}&group=${encodeURIComponent(grp.value)}`);
    if (!res.ok) { list.innerHTML = `<p class="small" style="color:var(--err)">불러오지 못했어요. 다시 시도해 주세요.</p>`; return; }
    const data = await res.json();
    items = data.items;
    items.forEach((p) => (cache[p.id] = p));
    if (!groupsLoaded) { data.groups.forEach((g) => grp.insertAdjacentHTML("beforeend", `<option>${esc(g)}</option>`)); groupsLoaded = true; }
    draw();
  }
  function sync() {
    picked = [...list.querySelectorAll("[name=pick]:checked")].map((x) => +x.value).concat(picked.filter((id) => !items.some((p) => p.id === id)));
    if (single) picked = picked.slice(0, 1);
    count.textContent = `고른 지문 ${picked.length}개`;
    ok.disabled = !picked.length;
  }
  list.addEventListener("change", sync);
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
  grp.addEventListener("change", load);
  bar.querySelector("button").addEventListener("click", () => { picked = []; sync(); dlg.showModal(); load(); q.focus(); });

  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "ok" || !picked.length) return;
    const chosen = picked.map((id) => cache[id]).filter(Boolean);
    if (single) { fill(single, null, chosen[0]); return; }
    const addBtn = document.getElementById("add-passage");
    let left = 0;
    for (const p of chosen) {
      let card = [...box.querySelectorAll(".passage-card")].find((c) => !c.querySelector("textarea").value.trim());
      if (!card && addBtn && !addBtn.disabled) { addBtn.click(); card = [...box.querySelectorAll(".passage-card")].pop(); }
      if (!card) { left++; continue; }
      fill(card.querySelector("textarea"), card.querySelector("input[name=p_title]"), p);
    }
    if (left) alert(`지문 칸이 모자라서 ${left}개는 넣지 못했어요. 한 번에 넣을 수 있는 지문 수를 넘었어요.`);
  });
  function fill(ta, titleInput, p) {
    ta.value = p.text;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    const t = titleInput || (single && document.querySelector("input[name=title]"));
    const wantSource = ta.closest("[data-source-title]");  // 출처 칸이면 출처를 먼저
    if (t && !t.value.trim()) { t.value = (wantSource && p.source) ? `${p.source}${p.title ? " · " + p.title : ""}` : p.title; t.dispatchEvent(new Event("input", { bubbles: true })); }
    ta.closest(".passage-card, .field")?.classList.add("picked-flash");
    setTimeout(() => ta.closest(".passage-card, .field")?.classList.remove("picked-flash"), 1200);
  }
})();
