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
      if (r.file_id) {
        out.innerHTML = `<a class="btn small" href="/files/${r.file_id}" target="_blank" rel="noopener">${escapeHtml(r.filename || "파일 열기")} 열기</a>`;
      } else if (r.reply) {
        out.innerHTML = `<p style="margin:6px 0 0">${escapeHtml(r.reply)}</p><span class="muted">모델: ${escapeHtml(r.model || "")}</span>`;
      }
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
