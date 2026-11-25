/* 미래샘 학부모 앱 서비스 워커: /p/sw.js 로 열림 (범위 /p/).
   선생님이 기록·공지를 올리면 서버가 보낸 푸시를 알림으로 띄우고, 누르면 학생 화면을 엽니다. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  const title = data.title || "미래샘 영어연구소";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "새 소식이 올라왔어요.",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: data.url || "/p/" },
    // 같은 날 다시 보내도 새 알림으로 뜨게, 알림마다 다른 표시(tag)를 씀
    tag: (data.url || "mirae") + "#" + Date.now(),
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/p/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (w.url.split("#")[0] === url.split("#")[0] && "focus" in w) { await w.navigate(url).catch(() => {}); return w.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});
