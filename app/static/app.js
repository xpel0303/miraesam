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
  const SEL = 'textarea[name="p_text"], textarea[name="passage"]';
  function attach(root) {
    (root.querySelectorAll ? root : document).querySelectorAll(SEL).forEach((ta) => {
      const prev = ta.previousElementSibling;
      if (prev && prev.classList && prev.classList.contains("ocr-bar")) return;
      ta.insertAdjacentHTML("beforebegin",
        '<div class="ocr-bar" style="display:flex;gap:8px;align-items:center;margin:0 0 4px">' +
        '<button type="button" class="btn ghost small ocr-btn" style="padding:3px 10px;font-size:12.5px">📄 PDF·사진에서 지문 가져오기</button>' +
        '<span class="small muted ocr-msg"></span></div>');
    });
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".ocr-btn");
    if (!btn) return;
    const bar = btn.closest(".ocr-bar"), ta = bar.nextElementSibling, msg = bar.querySelector(".ocr-msg");
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = "application/pdf,image/*";
    inp.onchange = async () => {
      if (!inp.files.length) return;
      const fd = new FormData();
      for (const f of inp.files) fd.append("files", f);
      btn.disabled = true; msg.style.color = ""; msg.textContent = "글자로 바꾸는 중… (쪽당 10~20초)";
      try {
        const r = await fetch("/api/ocr", { method: "POST", body: fd });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.text) throw new Error(j.error || "가져오지 못했어요.");
        const parts = j.passages && j.passages.length ? j.passages : [j.text];
        // 지문 여러 개 + '지문 추가' 버튼이 있으면 칸을 늘려 하나씩 넣음
        const add = document.getElementById("add-passage");
        if (parts.length > 1 && add && ta.name === "p_text") {
          let cur = ta;
          ta.value = (ta.value.trim() ? ta.value.trim() + "\n\n" : "") + parts[0];
          for (const t of parts.slice(1)) {
            const all = () => Array.from(document.querySelectorAll('textarea[name="p_text"]'));
            let next = all()[all().indexOf(cur) + 1];
            if (!next || next.value.trim()) { const n = all().length; add.click(); attach(document); next = all()[n] || all()[all().length - 1]; }
            if (!next || next === cur) { cur.value += "\n\n" + t; continue; }
            next.value = t; next.dispatchEvent(new Event("input", { bubbles: true })); cur = next;
          }
          msg.textContent = `지문 ${parts.length}개를 칸마다 넣었어요. 확인해 주세요.`;
        } else {
          ta.value = (ta.value.trim() ? ta.value.trim() + "\n\n" : "") + parts.join("\n\n");
          msg.textContent = parts.length > 1 ? `지문 ${parts.length}개를 넣었어요. 확인해 주세요.` : "넣었어요. 틀린 글자가 없는지 확인해 주세요.";
        }
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      } catch (err) {
        msg.style.color = "var(--err)"; msg.textContent = err.message;
      } finally {
        btn.disabled = false;
        document.querySelectorAll(".ocr-bar").forEach((b) => { if (b !== bar) { b.querySelector(".ocr-btn").disabled = false; b.querySelector(".ocr-msg").textContent = ""; } });
      }
    };
    inp.click();
  });
  attach(document);
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && attach(n)))).observe(document.body, { childList: true, subtree: true });
})();
