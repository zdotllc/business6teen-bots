"""FastAPI web dashboard for Agent Company AI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from agent_company_ai.core.company import Company

logger = logging.getLogger("agent_company_ai.dashboard")

STATIC_DIR = Path(__file__).parent / "static"

_app = FastAPI(title="Agent Company AI Dashboard")
_company: Company | None = None
_company_slug: str = "default"
_websockets: list[WebSocket] = []


async def _broadcast_ws(event: str, data: dict) -> None:
    """Send an event to all connected WebSocket clients."""
    payload = json.dumps({"event": event, "data": data})
    disconnected = []
    for ws in list(_websockets):  # iterate a copy to avoid mutation during loop
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        try:
            _websockets.remove(ws)
        except ValueError:
            pass  # already removed


async def _event_handler(event: str, data: dict) -> None:
    """Bridge company events to WebSocket clients."""
    await _broadcast_ws(event, data)


@_app.on_event("startup")
async def startup():
    global _company
    _company = await Company.load(company=_company_slug)
    _company.set_event_handler(_event_handler)
    logger.info(f"Dashboard started for '{_company.config.name}'")


@_app.on_event("shutdown")
async def shutdown():
    if _company:
        await _company.shutdown()


# ------------------------------------------------------------------
# Auth — multi-user dashboard access
# ------------------------------------------------------------------
# Users live in dashboard_users.json next to the package. On first start
# the default admin account is created:  admin / admin123
# (the password MUST be changed on first login). Admins can add/remove
# users from /users. Sessions are HMAC-signed cookies (7 days).

COOKIE_NAME = "mc_auth"
SESSION_DAYS = 7
DEFAULT_ADMIN = "admin"
DEFAULT_ADMIN_PW = "admin123"
_PBKDF2_ITERS = 200_000


def _users_path() -> Path:
    return Path(__file__).resolve().parents[3] / "dashboard_users.json"


def _load_users() -> dict:
    path = _users_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            logger.error("Could not read %s — starting with defaults", path)
    return {}


def _save_users(users: dict) -> None:
    path = _users_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(users, indent=2))
    tmp.replace(path)


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERS)
    return h.hex(), salt


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERS)
    return hmac.compare_digest(h.hex(), expected_hash)


def _get_users() -> dict:
    users = _load_users()
    if "secret" not in users:
        users["secret"] = secrets.token_hex(32)
    if not users.get("users"):
        h, salt = _hash_password(DEFAULT_ADMIN_PW)
        users["users"] = {
            DEFAULT_ADMIN: {
                "username": DEFAULT_ADMIN,
                "hash": h,
                "salt": salt,
                "role": "admin",
                "must_change_password": True,
                "created_at": time.time(),
            }
        }
        _save_users(users)
        logger.warning(
            "Dashboard: created default admin '%s' with password '%s' — change it on first login",
            DEFAULT_ADMIN, DEFAULT_ADMIN_PW,
        )
    return users


def _get_user(username: str) -> dict | None:
    return _get_users()["users"].get(username)


def _auth_secret() -> bytes:
    return _get_users()["secret"].encode("ascii")


def _make_token(username: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({
        "u": username,
        "exp": int(time.time()) + SESSION_DAYS * 86400,
        "n": secrets.token_hex(8),
    }).encode()).decode().rstrip("=")
    sig = hmac.new(_auth_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _session_user(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    try:
        expected = hmac.new(_auth_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data.get("exp", 0) <= time.time():
            return None
        return _get_user(data.get("u", ""))
    except Exception:
        return None


def _public_user(u: dict) -> dict:
    return {
        "username": u["username"],
        "role": u.get("role", "viewer"),
        "must_change_password": bool(u.get("must_change_password")),
    }


_AUTH_CSS = """
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    background: radial-gradient(900px 500px at 75% -10%, #1c2433 0%, #0b0f17 60%); color:#e6ecf7; padding:30px 16px; }
  .card { background:#121826; border:1px solid #232c40; border-radius:16px; padding:28px; max-width:520px; margin:0 auto; }
  h1 { font-size:19px; margin:0 0 6px; text-align:center; }
  p.sub { color:#8b97ad; font-size:13px; text-align:center; margin:0 0 20px; }
  input, select { width:100%; padding:10px 12px; border-radius:9px; border:1px solid #232c40; background:#0b0f17;
    color:#e6ecf7; font-size:14px; margin-bottom:10px; font-family:inherit; }
  input:focus, select:focus { outline:2px solid #FACC0F; border-color:transparent; }
  button { padding:11px; border:0; border-radius:9px; background:#FACC0F; color:#171204; font-weight:700;
    font-size:14px; cursor:pointer; width:100%; }
  button:hover { filter:brightness(1.1); }
  .err { color:#ef4444; font-size:13px; margin-top:10px; text-align:center; }
  .ok { color:#22c55e; font-size:13px; margin-top:10px; text-align:center; }
  table { width:100%; border-collapse:collapse; font-size:14px; margin-top:6px; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid #232c40; }
  th { color:#8b97ad; font-size:12px; text-transform:uppercase; }
  .actions button { width:auto; padding:6px 10px; font-size:12px; margin-right:6px; }
  .actions button.danger { background:#ef4444; color:#fff; }
  a { color:#FACC0F; text-decoration:none; }
  .row { display:flex; gap:8px; }
  .row input, .row select { flex:1; }
"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{_AUTH_CSS}</style>
</head>
<body>{body}</body>
</html>"""


def _login_body(error: bool = False) -> str:
    err = '<div class="err">Invalid username or password</div>' if error else ""
    return f"""<div class="card">
  <h1>🤖 Mission Control</h1>
  <p class="sub">Sign in to access your bots</p>
  <form method="post" action="/login">
    <input type="text" name="username" placeholder="Username" autofocus autocomplete="username" />
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" />
    <button type="submit">Sign in</button>
    {err}
  </form>
</div>"""


@_app.get("/login")
async def login_page(error: bool = False):
    return HTMLResponse(_page("Mission Control — Sign in", _login_body(error)))


@_app.post("/login")
async def login_submit(request: Request):
    from urllib.parse import parse_qs

    raw = (await request.body()).decode("utf-8", "replace")
    params = parse_qs(raw)
    username = (params.get("username") or [""])[0].strip()
    password = (params.get("password") or [""])[0]
    user = _get_user(username)
    if not user or not _verify_password(password, user["salt"], user["hash"]):
        return HTMLResponse(_page("Mission Control — Sign in", _login_body(error=True)), status_code=401)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME, _make_token(username), httponly=True, samesite="lax",
        max_age=SESSION_DAYS * 86400, path="/",
    )
    return resp


@_app.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@_app.get("/change-password")
async def change_password_page(request: Request):
    user = _session_user(request.cookies.get(COOKIE_NAME, ""))
    forced = bool(user and user.get("must_change_password"))
    note = "You must change your password before continuing." if forced else "Update your password."
    return HTMLResponse(_page("Mission Control — Change password", f"""<div class="card">
  <h1>🔑 Change password</h1>
  <p class="sub">{note}</p>
  <form id="pwForm">
    <input type="password" id="cur" placeholder="Current password" autocomplete="current-password" />
    <input type="password" id="new1" placeholder="New password (min 8 chars)" autocomplete="new-password" />
    <input type="password" id="new2" placeholder="Repeat new password" autocomplete="new-password" />
    <button type="submit">Save password</button>
    <div id="msg"></div>
  </form>
</div>
<script>
document.getElementById('pwForm').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const msg = document.getElementById('msg');
  const n1 = document.getElementById('new1').value;
  const n2 = document.getElementById('new2').value;
  if (n1 !== n2) {{ msg.className='err'; msg.textContent='Passwords do not match'; return; }}
  const r = await fetch('/api/auth/change-password', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{ current: document.getElementById('cur').value, new_password: n1 }})
  }});
  const d = await r.json().catch(() => ({{}}));
  if (r.ok) {{ msg.className='ok'; msg.textContent='Password updated'; setTimeout(() => location.href='/', 700); }}
  else {{ msg.className='err'; msg.textContent = d.error || 'Failed'; }}
}});
</script>"""))


@_app.post("/api/auth/change-password")
async def api_change_password(request: Request):
    user = _session_user(request.cookies.get(COOKIE_NAME, ""))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    current = str(body.get("current", ""))
    newpw = str(body.get("new_password", ""))
    if not _verify_password(current, user["salt"], user["hash"]):
        return JSONResponse({"error": "Current password is incorrect"}, status_code=400)
    if len(newpw) < 8:
        return JSONResponse({"error": "New password must be at least 8 characters"}, status_code=400)
    data = _get_users()
    u = data["users"][user["username"]]
    h, salt = _hash_password(newpw)
    u["hash"] = h
    u["salt"] = salt
    u["must_change_password"] = False
    _save_users(data)
    return {"ok": True}


@_app.get("/api/auth/me")
async def api_me(request: Request):
    user = _session_user(request.cookies.get(COOKIE_NAME, ""))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return _public_user(user)


def _require_admin(request: Request):
    user = _session_user(request.cookies.get(COOKIE_NAME, ""))
    if not user:
        return None
    return user if user.get("role") == "admin" else False


@_app.get("/api/users")
async def api_users(request: Request):
    admin = _require_admin(request)
    if admin is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if admin is False:
        return JSONResponse({"error": "Admin required"}, status_code=403)
    data = _get_users()
    return [_public_user(u) for u in data["users"].values()]


@_app.post("/api/users")
async def api_create_user(request: Request):
    admin = _require_admin(request)
    if admin is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if admin is False:
        return JSONResponse({"error": "Admin required"}, status_code=403)
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    role = body.get("role") if body.get("role") in ("admin", "viewer") else "viewer"
    if not username or not password:
        return JSONResponse({"error": "Username and password required"}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)
    data = _get_users()
    if username in data["users"]:
        return JSONResponse({"error": "Username already exists"}, status_code=409)
    h, salt = _hash_password(password)
    data["users"][username] = {
        "username": username, "hash": h, "salt": salt, "role": role,
        "must_change_password": True, "created_at": time.time(),
    }
    _save_users(data)
    return _public_user(data["users"][username])


@_app.delete("/api/users/{username}")
async def api_delete_user(username: str, request: Request):
    admin = _require_admin(request)
    if admin is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if admin is False:
        return JSONResponse({"error": "Admin required"}, status_code=403)
    data = _get_users()
    if username not in data["users"]:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if username == admin["username"]:
        return JSONResponse({"error": "You cannot delete your own account"}, status_code=400)
    if data["users"][username].get("role") == "admin" and sum(
        1 for u in data["users"].values() if u.get("role") == "admin"
    ) <= 1:
        return JSONResponse({"error": "Cannot delete the last admin"}, status_code=400)
    del data["users"][username]
    _save_users(data)
    return {"ok": True}


@_app.post("/api/users/{username}/reset-password")
async def api_reset_password(username: str, request: Request):
    admin = _require_admin(request)
    if admin is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if admin is False:
        return JSONResponse({"error": "Admin required"}, status_code=403)
    body = await request.json()
    newpw = str(body.get("password", ""))
    if len(newpw) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)
    data = _get_users()
    if username not in data["users"]:
        return JSONResponse({"error": "User not found"}, status_code=404)
    h, salt = _hash_password(newpw)
    u = data["users"][username]
    u["hash"] = h
    u["salt"] = salt
    u["must_change_password"] = True
    _save_users(data)
    return {"ok": True}


@_app.get("/users")
async def users_page(request: Request):
    admin = _require_admin(request)
    if admin is None:
        return RedirectResponse(url="/login", status_code=303)
    if admin is False:
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(_page("Mission Control — Users", """<div class="card">
  <h1>👥 Dashboard Users</h1>
  <p class="sub">Add or remove people who can access Mission Control.</p>
  <h2 style="font-size:15px;margin:14px 0 8px;">Add user</h2>
  <form id="addForm" class="row">
    <input type="text" id="u" placeholder="Username" />
    <input type="password" id="p" placeholder="Password (min 8)" />
    <select id="r">
      <option value="viewer">Viewer</option>
      <option value="admin">Admin</option>
    </select>
    <button type="submit" style="width:auto;padding:10px 14px;">Add</button>
  </form>
  <div id="msg"></div>
  <table>
    <thead><tr><th>Username</th><th>Role</th><th>Status</th><th></th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>
<script>
const api = (u, o) => fetch(u, o).then(r => r.json().then(d => ({ ok: r.ok, d })));
async function load() {
  const { ok, d } = await api('/api/users');
  if (!ok) return;
  const rows = document.getElementById('rows');
  rows.innerHTML = '';
  d.forEach(u => {
    const tr = document.createElement('tr');
    const status = u.must_change_password ? '<span style="color:#FACC0F">must change</span>' : 'ok';
    const reset = '<button onclick="resetPw(\'' + u.username + '\')" style="width:auto;padding:5px 9px;font-size:12px;">Reset pw</button>';
    const del = '<button class="danger" onclick="delUser(\'' + u.username + '\')" style="width:auto;padding:5px 9px;font-size:12px;">Delete</button>';
    tr.innerHTML = '<td><b>' + u.username + '</b></td><td>' + u.role + '</td><td>' + status + '</td><td class="actions">' + reset + del + '</td>';
    rows.appendChild(tr);
  });
}
document.getElementById('addForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = document.getElementById('msg');
  const r = await api('/api/users', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ username: document.getElementById('u').value, password: document.getElementById('p').value, role: document.getElementById('r').value }) });
  msg.className = r.ok ? 'ok' : 'err';
  msg.textContent = r.ok ? ('Added ' + r.d.username) : (r.d.error || 'Failed');
  if (r.ok) { document.getElementById('u').value=''; document.getElementById('p').value=''; load(); }
});
async function resetPw(username) {
  const pw = prompt('New password for ' + username + ' (min 8 chars):');
  if (!pw) return;
  const r = await api('/api/users/' + encodeURIComponent(username) + '/reset-password', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ password: pw }) });
  alert(r.ok ? 'Password reset — they must change it on next login' : (r.d.error || 'Failed'));
}
async function delUser(username) {
  if (!confirm('Delete user ' + username + '?')) return;
  const r = await api('/api/users/' + encodeURIComponent(username), { method:'DELETE' });
  alert(r.ok ? 'Deleted' : (r.d.error || 'Failed'));
  load();
}
load();
</script>"""))


# ─── Nav bar injected into dashboard pages ─────────────────────
def _nav_html(request: Request) -> str:
    user = _session_user(request.cookies.get(COOKIE_NAME, ""))
    items = [
        '<a href="/" style="font-size:12px;padding:6px 10px;border:1px solid #444;border-radius:8px;background:#222;">Mission Control</a>',
        '<a href="/main" style="font-size:12px;padding:6px 10px;border:1px solid #444;border-radius:8px;background:#222;">Main dashboard</a>',
        '<a href="/change-password" style="font-size:12px;padding:6px 10px;border:1px solid #444;border-radius:8px;background:#222;">Change password</a>',
    ]
    if user and user.get("role") == "admin":
        items.append('<a href="/users" style="font-size:12px;padding:6px 10px;border:1px solid #444;border-radius:8px;background:#222;">Users</a>')
    logout = ('<form action="/logout" method="post" style="display:inline">'
              '<button type="submit" style="width:auto;padding:6px 10px;font-size:12px;background:transparent;border:1px solid #444;border-radius:8px;">Log out</button></form>')
    return ('<div style="position:fixed;top:10px;right:14px;z-index:99999;display:flex;gap:8px;align-items:center;">'
            + "".join(items) + logout + "</div>")


def _serve_page(filename: str, request: Request) -> HTMLResponse:
    html = (STATIC_DIR / filename).read_text()
    if "</body>" in html:
        html = html.replace("</body>", _nav_html(request) + "</body>")
    return HTMLResponse(html)


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/login":
            return await call_next(request)
        user = _session_user(request.cookies.get(COOKIE_NAME, ""))
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return RedirectResponse(url="/login", status_code=303)
        # Force password change on first login.
        if user.get("must_change_password") and path not in {
            "/change-password", "/logout", "/login",
            "/api/auth/change-password", "/api/auth/me",
        }:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Password change required"}, status_code=403)
            return RedirectResponse(url="/change-password", status_code=303)
        # User management requires an admin.
        if (path == "/users" or path.startswith("/api/users")) and user.get("role") != "admin":
            if path.startswith("/api/"):
                return JSONResponse({"error": "Admin required"}, status_code=403)
            return RedirectResponse(url="/", status_code=303)
        return await call_next(request)


_app.add_middleware(_AuthMiddleware)


# ------------------------------------------------------------------
# API routes
# ------------------------------------------------------------------


@_app.get("/")
async def index(request: Request):
    # Mission Control is the default dashboard.
    return _serve_page("advanced.html", request)


@_app.get("/style.css")
async def style():
    from fastapi.responses import Response
    return Response(
        content=(STATIC_DIR / "style.css").read_text(),
        media_type="text/css",
    )


@_app.get("/app.js")
async def app_js():
    from fastapi.responses import Response
    return Response(
        content=(STATIC_DIR / "app.js").read_text(),
        media_type="application/javascript",
    )


# ---- Mission Control (advanced dashboard: robot faces + voice chat) ----

@_app.get("/main")
async def main_dashboard(request: Request):
    """The classic main dashboard (in addition to Mission Control at /)."""
    return _serve_page("index.html", request)


@_app.get("/advanced")
async def advanced(request: Request):
    return _serve_page("advanced.html", request)


@_app.get("/advanced.css")
async def advanced_css():
    from fastapi.responses import Response
    return Response(
        content=(STATIC_DIR / "advanced.css").read_text(),
        media_type="text/css",
    )


@_app.get("/advanced.js")
async def advanced_js():
    from fastapi.responses import Response
    return Response(
        content=(STATIC_DIR / "advanced.js").read_text(),
        media_type="application/javascript",
    )


@_app.get("/api/status")
async def api_status():
    return _company.status() if _company else {}


@_app.get("/api/agents")
async def api_agents():
    return _company.list_agents() if _company else []


@_app.get("/api/org-chart")
async def api_org_chart():
    return _company.get_org_chart() if _company else {}


@_app.get("/api/tasks")
async def api_tasks():
    if not _company:
        return []
    return [t.to_dict() for t in _company.task_board.list_all()]


@_app.post("/api/tasks")
async def api_create_task(body: dict):
    if not _company:
        return {"error": "Company not loaded"}
    task = await _company.assign(
        description=body["description"],
        assignee=body.get("assignee"),
    )
    return task.to_dict()


# NOTE: the specific /api/chat/group route must be registered BEFORE the
# parameterized /api/chat/{agent_name}, or 'group' is captured as an agent name.

@_app.post("/api/chat/group")
async def api_chat_group(body: dict):
    """Group chat: one message to several (or all) agents, replies collected."""
    if not _company:
        return {"error": "Company not loaded"}
    message = str(body.get("message", ""))
    if not message.strip():
        return {"error": "A message is required"}
    all_names = [a["name"] for a in _company.list_agents()]
    wanted = body.get("agents")
    names = [n for n in all_names if n in wanted] if isinstance(wanted, list) else all_names
    if not names:
        return {"error": "No agents available to message"}
    replies = await _company.chat_many(names, message)
    return {"replies": replies}


@_app.post("/api/chat/{agent_name}")
async def api_chat(agent_name: str, body: dict):
    if not _company:
        return {"error": "Company not loaded"}
    try:
        reply = await _company.chat(agent_name, body["message"])
        return {"reply": reply}
    except ValueError as e:
        return {"error": str(e)}


_goal_task: asyncio.Task | None = None


@_app.post("/api/goal")
async def api_run_goal(body: dict):
    global _goal_task
    if not _company:
        return {"error": "Company not loaded"}
    goal = body.get("goal", "")
    _goal_task = asyncio.create_task(_company.run_goal(goal))

    def _on_goal_done(t: asyncio.Task) -> None:
        if t.cancelled():
            logger.warning(f"Goal cancelled: {goal[:60]}")
        elif t.exception():
            logger.error(f"Goal failed with error: {t.exception()}")
        else:
            logger.info(f"Goal completed: {goal[:60]}")

    _goal_task.add_done_callback(_on_goal_done)
    return {"status": "started", "goal": goal}


@_app.post("/api/stop")
async def api_stop():
    if not _company:
        return {"error": "Company not loaded"}
    _company.request_stop()
    return {"status": "stop_requested"}


@_app.post("/api/hire")
async def api_hire(body: dict):
    if not _company:
        return {"error": "Company not loaded"}
    try:
        agent = await _company.hire(
            role_name=body["role"],
            agent_name=body.get("name"),
            provider=body.get("provider"),
        )
        return {"name": agent.name, "role": agent.role.name, "title": agent.role.title}
    except Exception as e:
        return {"error": str(e)}


@_app.get("/api/cost")
async def api_cost():
    if not _company:
        return {}
    return _company.cost_tracker.summary()


@_app.get("/api/cost/recent")
async def api_cost_recent():
    if not _company:
        return []
    return _company.cost_tracker.recent(limit=50)


@_app.get("/api/messages")
async def api_messages():
    if not _company:
        return []
    history = _company.bus.get_history(limit=100)
    return [
        {
            "from": m.from_agent,
            "to": m.to_agent,
            "content": m.content,
            "topic": m.topic,
            "timestamp": m.timestamp.isoformat(),
        }
        for m in history
    ]


@_app.get("/api/artifacts")
async def api_artifacts(task_id: str | None = Query(None)):
    if not _company:
        return []
    return await _company.get_artifacts(task_id=task_id)


@_app.get("/api/artifacts/{artifact_id}")
async def api_artifact(artifact_id: str):
    if not _company:
        return {"error": "Company not loaded"}
    row = await _company.db.fetch_one(
        "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
    )
    if row is None:
        return {"error": "Artifact not found"}
    return row


@_app.get("/api/output-dir")
async def api_output_dir():
    if not _company:
        return {"error": "Company not loaded"}
    return {"output_dir": str(_company.output_dir)}


# ------------------------------------------------------------------
# ProfitEngine API
# ------------------------------------------------------------------


@_app.get("/api/profit-engine")
async def api_profit_engine():
    """Return the current ProfitEngine configuration."""
    if not _company:
        return {"error": "Company not loaded"}
    pe = _company.config.profit_engine
    return pe.model_dump()


@_app.post("/api/profit-engine")
async def api_profit_engine_update(body: dict):
    """Update ProfitEngine fields and save to config.yaml."""
    if not _company:
        return {"error": "Company not loaded"}

    from agent_company_ai.config import save_config

    pe = _company.config.profit_engine
    valid_fields = {
        "enabled", "mission", "revenue_streams", "target_customers",
        "pricing_model", "competitive_edge", "key_metrics",
        "cost_priorities", "additional_context",
    }
    for key, value in body.items():
        if key in valid_fields:
            setattr(pe, key, value)

    save_config(_company.config, _company.company_dir / "config.yaml")
    return pe.model_dump()


@_app.get("/api/profit-engine/templates")
async def api_profit_engine_templates():
    """List all ProfitEngine templates with their full content."""
    from agent_company_ai.config import list_profit_engine_templates, load_profit_engine_template

    result = []
    for name in list_profit_engine_templates():
        tmpl = load_profit_engine_template(name)
        result.append(tmpl)
    return result


# ------------------------------------------------------------------
# Wallet API (read-only)
# ------------------------------------------------------------------


@_app.get("/api/wallet/balance")
async def api_wallet_balance(chain: str | None = Query(None)):
    if not _company:
        return {"error": "Company not loaded"}
    return _company.wallet_manager.get_balance(chain_name=chain)


@_app.get("/api/wallet/address")
async def api_wallet_address():
    if not _company:
        return {"error": "Company not loaded"}
    addr = _company.wallet_manager.address
    if addr is None:
        return {"address": None, "error": "No wallet found"}
    return {"address": addr}


@_app.get("/api/wallet/payments")
async def api_wallet_payments(status: str | None = Query(None)):
    if not _company:
        return {"error": "Company not loaded"}
    return await _company.wallet_manager.list_payments(status=status)


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------


@_app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not _session_user(ws.cookies.get(COOKIE_NAME, "")):
        await ws.close(code=4401)
        return
    await ws.accept()
    _websockets.append(ws)
    try:
        while True:
            data = await ws.receive_text()
            # Client can send commands via WS too
            try:
                msg = json.loads(data)
                if msg.get("action") == "chat" and _company:
                    reply = await _company.chat(msg["agent"], msg["message"])
                    await ws.send_text(json.dumps({
                        "event": "chat.reply",
                        "data": {"agent": msg["agent"], "reply": reply},
                    }))
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        try:
            _websockets.remove(ws)
        except ValueError:
            pass


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------


def run_dashboard(host: str = "127.0.0.1", port: int = 8420, company: str = "default") -> None:
    global _company_slug
    _company_slug = company
    uvicorn.run(_app, host=host, port=port, log_level="info")
