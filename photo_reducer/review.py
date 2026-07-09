"""Stage 4: browser-based review UI. Pure stdlib HTTP server, no frameworks.

Decisions are written to the DB immediately on click, so closing the browser
mid-review never loses anything.
"""

import json
import mimetypes
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db

PAGE_SIZE = 50


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _effective_decision(decision: str | None, proposal: str) -> str:
    if decision:
        return decision
    return "archive" if proposal == "archive" else "keep"


def _load_clusters(conn) -> list[dict]:
    clusters = conn.execute("SELECT * FROM clusters ORDER BY id").fetchall()
    result = []

    for c in clusters:
        members = conn.execute(
            """
            SELECT a.*, cm.phash, cm.rank, cm.proposal, d.decision
            FROM cluster_members cm
            JOIN assets a ON a.uuid = cm.asset_uuid
            LEFT JOIN decisions d ON d.asset_uuid = a.uuid
            WHERE cm.cluster_id = ?
            ORDER BY cm.rank
            """,
            (c["id"],),
        ).fetchall()

        member_list = []
        savings = 0
        for m in members:
            effective = _effective_decision(m["decision"], m["proposal"])
            if effective == "archive":
                savings += m["original_filesize"] or 0
            member_list.append(
                {
                    "uuid": m["uuid"],
                    "kind": m["kind"],
                    "filesize": m["original_filesize"],
                    "duration": m["duration"],
                    "is_favorite": bool(m["is_favorite"]),
                    "is_edited": bool(m["is_edited"]),
                    "score_overall": m["score_overall"],
                    "proposal": m["proposal"],
                    "effective": effective,
                    "has_preview": bool(m["preview_path"]),
                }
            )

        if not member_list:
            continue

        result.append(
            {
                "id": c["id"],
                "start_time": c["start_time"],
                "end_time": c["end_time"],
                "item_count": c["item_count"],
                "savings_bytes": savings,
                "members": member_list,
            }
        )

    result.sort(key=lambda c: c["savings_bytes"], reverse=True)
    return result


HTML_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Photo Reducer - Review</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0;
         background: #f5f5f7; color: #1d1d1f; }
  header { position: sticky; top: 0; background: #fff; border-bottom: 1px solid #ddd;
           padding: 12px 20px; display: flex; align-items: center; gap: 20px;
           z-index: 10; flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; }
  #summary { font-size: 14px; color: #444; }
  #summary b { color: #d33; }
  .spacer { flex: 1; }
  button { cursor: pointer; border: 1px solid #ccc; background: #fff; border-radius: 6px;
           padding: 6px 12px; font-size: 13px; }
  button.primary { background: #0071e3; color: #fff; border-color: #0071e3; }
  .cluster { background: #fff; margin: 16px 20px; border-radius: 10px; padding: 14px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .cluster-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .cluster-head .meta { font-size: 13px; color: #666; }
  .cluster-head .savings { font-weight: 600; color: #b8860b; }
  .thumbs { display: flex; flex-wrap: wrap; gap: 8px; }
  .thumb { position: relative; width: 140px; cursor: pointer; border-radius: 8px;
           overflow: hidden; border: 4px solid transparent; }
  .thumb.keep { border-color: #34c759; }
  .thumb.archive { border-color: #ff3b30; opacity: 0.6; }
  .thumb img { width: 100%; height: 140px; object-fit: cover; display: block;
               background: #eee; }
  .thumb .badges { position: absolute; top: 2px; left: 2px; display: flex; gap: 3px; }
  .thumb .badge { background: rgba(0,0,0,0.6); color: #fff; font-size: 10px;
                  padding: 1px 4px; border-radius: 4px; }
  .thumb .info { font-size: 10px; color: #333; padding: 2px 4px; text-align: center; }
  #pagination { display: flex; justify-content: center; gap: 10px; padding: 20px; }
  #loading { text-align: center; padding: 40px; color: #888; }
</style>
</head>
<body>
<header>
  <h1>Photo Reducer</h1>
  <div id="summary">Loading...</div>
  <div class="spacer"></div>
  <button id="acceptPage">Accept all proposals on this page</button>
</header>
<div id="clusters"><div id="loading">Loading...</div></div>
<div id="pagination"></div>

<script>
let page = 0;
let clustersOnPage = [];

function fmtBytes(n) {
  const units = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + ' ' + units[i];
}

function fmtDuration(s) {
  if (!s) return '';
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m + ':' + String(sec).padStart(2, '0');
}

async function loadSummary() {
  const r = await fetch('/api/summary');
  const data = await r.json();
  document.getElementById('summary').innerHTML =
    `<b>${data.archive_count}</b> items to archive &mdash; <b>${fmtBytes(data.archive_bytes)}</b> recoverable`;
}

function thumbEl(cluster, m) {
  const div = document.createElement('div');
  div.className = 'thumb ' + m.effective;
  div.dataset.uuid = m.uuid;
  div.dataset.cluster = cluster.id;

  const img = document.createElement('img');
  img.src = m.has_preview ? ('/thumb/' + m.uuid) : '';
  img.loading = 'lazy';
  div.appendChild(img);

  const badges = document.createElement('div');
  badges.className = 'badges';
  if (m.kind === 'video') badges.innerHTML += `<span class="badge">VIDEO ${fmtDuration(m.duration)}</span>`;
  if (m.is_favorite) badges.innerHTML += `<span class="badge">★</span>`;
  if (m.is_edited) badges.innerHTML += `<span class="badge">edited</span>`;
  div.appendChild(badges);

  const info = document.createElement('div');
  info.className = 'info';
  info.textContent = fmtBytes(m.filesize || 0);
  div.appendChild(info);

  div.onclick = () => toggle(div, cluster.id, m.uuid);
  return div;
}

async function toggle(div, clusterId, uuid) {
  const newDecision = div.classList.contains('keep') ? 'archive' : 'keep';
  div.classList.remove('keep', 'archive');
  div.classList.add(newDecision);
  await fetch('/decide', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({uuid, decision: newDecision, cluster_id: clusterId})
  });
  loadSummary();
}

async function keepAll(clusterId, container) {
  await fetch('/decide-cluster', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cluster_id: clusterId, decision: 'keep'})
  });
  container.querySelectorAll('.thumb').forEach(t => {
    t.classList.remove('keep', 'archive');
    t.classList.add('keep');
  });
  loadSummary();
}

function clusterEl(cluster) {
  const div = document.createElement('div');
  div.className = 'cluster';

  const head = document.createElement('div');
  head.className = 'cluster-head';
  const date = new Date(cluster.start_time).toLocaleString();
  head.innerHTML = `<div class="meta">${date} &middot; ${cluster.item_count} items</div>
    <div class="savings">${fmtBytes(cluster.savings_bytes)} recoverable</div>`;
  const keepAllBtn = document.createElement('button');
  keepAllBtn.textContent = 'Keep all';
  keepAllBtn.onclick = () => keepAll(cluster.id, thumbsDiv);
  head.appendChild(keepAllBtn);
  div.appendChild(head);

  const thumbsDiv = document.createElement('div');
  thumbsDiv.className = 'thumbs';
  cluster.members.forEach(m => thumbsDiv.appendChild(thumbEl(cluster, m)));
  div.appendChild(thumbsDiv);

  return div;
}

async function loadPage(p) {
  page = p;
  const container = document.getElementById('clusters');
  container.innerHTML = '<div id="loading">Loading...</div>';
  const r = await fetch('/api/clusters?page=' + p);
  const data = await r.json();
  clustersOnPage = data.clusters;
  container.innerHTML = '';
  if (clustersOnPage.length === 0) {
    container.innerHTML = '<div id="loading">No more moments to review.</div>';
  }
  clustersOnPage.forEach(c => container.appendChild(clusterEl(c)));

  const pag = document.getElementById('pagination');
  pag.innerHTML = '';
  if (p > 0) {
    const prev = document.createElement('button');
    prev.textContent = '← Previous';
    prev.onclick = () => loadPage(p - 1);
    pag.appendChild(prev);
  }
  if (data.has_more) {
    const next = document.createElement('button');
    next.textContent = 'Next →';
    next.onclick = () => loadPage(p + 1);
    pag.appendChild(next);
  }
}

document.getElementById('acceptPage').onclick = async () => {
  for (const c of clustersOnPage) {
    await fetch('/accept-cluster', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cluster_id: c.id})
    });
  }
  loadPage(page);
  loadSummary();
};

loadSummary();
loadPage(0);
</script>
</body>
</html>
"""


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/":
                body = HTML_PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/clusters":
                qs = parse_qs(parsed.query)
                page = int(qs.get("page", ["0"])[0])
                conn = db.connect()
                clusters = _load_clusters(conn)
                conn.close()
                start = page * PAGE_SIZE
                chunk = clusters[start : start + PAGE_SIZE]
                self._send_json(
                    {"clusters": chunk, "has_more": start + PAGE_SIZE < len(clusters)}
                )
                return

            if parsed.path == "/api/summary":
                conn = db.connect()
                clusters = _load_clusters(conn)
                conn.close()
                archive_bytes = sum(c["savings_bytes"] for c in clusters)
                archive_count = sum(
                    1
                    for c in clusters
                    for m in c["members"]
                    if m["effective"] == "archive"
                )
                self._send_json({"archive_bytes": archive_bytes, "archive_count": archive_count})
                return

            if parsed.path.startswith("/thumb/"):
                uuid = parsed.path[len("/thumb/") :]
                conn = db.connect()
                row = conn.execute(
                    "SELECT preview_path FROM assets WHERE uuid = ?", (uuid,)
                ).fetchone()
                conn.close()
                if not row or not row["preview_path"]:
                    self.send_response(404)
                    self.end_headers()
                    return
                path = row["preview_path"]
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                except OSError:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json({"error": "bad json"}, 400)
                return

            conn = db.connect()
            now = datetime.now().isoformat()

            if self.path == "/decide":
                conn.execute(
                    """
                    INSERT INTO decisions (asset_uuid, decision, decided_at, cluster_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(asset_uuid) DO UPDATE SET
                        decision=excluded.decision, decided_at=excluded.decided_at,
                        cluster_id=excluded.cluster_id
                    """,
                    (payload["uuid"], payload["decision"], now, payload.get("cluster_id")),
                )
                conn.commit()
                conn.close()
                self._send_json({"ok": True})
                return

            if self.path == "/decide-cluster":
                cluster_id = payload["cluster_id"]
                decision = payload["decision"]
                members = conn.execute(
                    "SELECT asset_uuid FROM cluster_members WHERE cluster_id = ?",
                    (cluster_id,),
                ).fetchall()
                for m in members:
                    conn.execute(
                        """
                        INSERT INTO decisions (asset_uuid, decision, decided_at, cluster_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(asset_uuid) DO UPDATE SET
                            decision=excluded.decision, decided_at=excluded.decided_at,
                            cluster_id=excluded.cluster_id
                        """,
                        (m["asset_uuid"], decision, now, cluster_id),
                    )
                conn.commit()
                conn.close()
                self._send_json({"ok": True})
                return

            if self.path == "/accept-cluster":
                cluster_id = payload["cluster_id"]
                members = conn.execute(
                    "SELECT asset_uuid, proposal FROM cluster_members WHERE cluster_id = ?",
                    (cluster_id,),
                ).fetchall()
                for m in members:
                    decision = "archive" if m["proposal"] == "archive" else "keep"
                    conn.execute(
                        """
                        INSERT INTO decisions (asset_uuid, decision, decided_at, cluster_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(asset_uuid) DO UPDATE SET
                            decision=excluded.decision, decided_at=excluded.decided_at,
                            cluster_id=excluded.cluster_id
                        """,
                        (m["asset_uuid"], decision, now, cluster_id),
                    )
                conn.commit()
                conn.close()
                self._send_json({"ok": True})
                return

            conn.close()
            self._send_json({"error": "not found"}, 404)

    return Handler


def run(port: int = 8765) -> None:
    handler = _make_handler()
    server = ThreadingHTTPServer(("localhost", port), handler)
    url = f"http://localhost:{port}/"
    print(f"Review UI running at {url}")
    print("Decisions save immediately - close the browser tab any time and resume later.")
    print("Press Ctrl+C here to stop the server.")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()
