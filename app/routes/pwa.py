"""홈 화면 앱(PWA): 앱 정보(manifest)와 서비스 워커.

사이트를 그대로 여는 방식이라 Render에 배포하면 앱도 바로 바뀜 (서비스 워커는 화면을 저장해 두지 않음).
학생은 자기 접속 코드 화면에서 설치하면 앱을 열 때 바로 그 화면으로 감.
"""
import json
import re

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

SW = """// 미래샘 앱 서비스 워커: 늘 서버에서 새로 받고, 인터넷이 끊겼을 때만 안내 화면
const OFFLINE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>미래샘</title><body style="font-family:sans-serif;text-align:center;padding:60px 20px;color:#1B2A5C;background:#FFF4E6">'
  + '<p style="font-size:3rem;margin:0">📡</p><h2>인터넷 연결이 끊겼어요</h2><p>연결된 뒤 다시 열어 주세요.</p>'
  + '<button onclick="location.reload()" style="padding:10px 20px;border-radius:99px;border:0;background:#1B2A5C;color:#fff;font-size:1rem">다시 시도</button></body>';
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {title: '미래샘', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || '미래샘', {
    body: d.body || '', tag: d.tag || undefined, renotify: !!d.tag, data: {url: d.url || '/'},
    icon: '/static/icons/icon-192.png', badge: '/static/icons/icon-192.png', lang: 'ko'
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate(url).catch(() => {}); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, {headers: {'Content-Type': 'text/html; charset=utf-8'}})));
});
"""


@router.get("/sw.js", include_in_schema=False)
def sw():
    return Response(SW, media_type="text/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest(s: str = ""):
    code = s if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s or "") else ""
    m = {
        "name": "미래샘 숙제" if code else "미래샘 영어연구소 (선생님)",
        "short_name": "미래샘 숙제" if code else "미래샘 교사",
        "id": f"/s/{code}" if code else "/",
        "start_url": f"/s/{code}" if code else "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#FFF4E6",
        "theme_color": "#1B2A5C",
        "lang": "ko",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(m, ensure_ascii=False), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/push/key", include_in_schema=False)
def push_key():
    from .. import push
    return {"key": push.keys()["public"]}
