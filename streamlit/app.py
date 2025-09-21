# app.py
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, cast

import requests
import streamlit as st


# =========================
# Page & Paths
# =========================
st.set_page_config(page_title="NeuroServe Dashboard", page_icon="🚀", layout="wide")

APP_DIR = Path(__file__).parent
STREAMLIT_DIR = APP_DIR / ".streamlit"
SERVERS_STORE = STREAMLIT_DIR / "servers.json"
CSS_PATH = STREAMLIT_DIR / "neuroserve.css"


# =========================
# CSS Loader (external file)
# =========================
@st.cache_data
def _load_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")

def apply_css(path: Path) -> None:
    if path.exists():
        st.markdown(f"<style>{_load_text(str(path))}</style>", unsafe_allow_html=True)
    else:
        st.warning(f"CSS file not found: {path}")

apply_css(CSS_PATH)


# =========================
# Storage Helpers
# =========================
def ensure_dirs() -> None:
    STREAMLIT_DIR.mkdir(parents=True, exist_ok=True)

def load_servers_from_disk() -> Dict[str, str]:
    """Return {} if file missing/invalid (لا قيم افتراضية)."""
    if SERVERS_STORE.exists():
        try:
            data = json.loads(SERVERS_STORE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return {}

def save_servers_to_disk(servers: Dict[str, str]) -> None:
    ensure_dirs()
    SERVERS_STORE.write_text(json.dumps(servers, indent=2, ensure_ascii=False), encoding="utf-8")


# =========================
# Session State
# =========================
def _init_state() -> None:
    if "servers" not in st.session_state:
        st.session_state.servers = load_servers_from_disk()  # Dict[name, base_url]
    if "selected_server" not in st.session_state:
        st.session_state.selected_server = ""  # لا اختيار تلقائي
    if "token_by_server" not in st.session_state:
        st.session_state["token_by_server"] = {}  # {base_url: token or None}
    if "last_response" not in st.session_state:
        st.session_state.last_response = None

_init_state()


# =========================
# Sidebar Actions (callbacks)
# =========================
def _save_update_server(new_name: str, new_url: str):
    if new_name.strip() and new_url.strip():
        # إعادة تسمية عند تغيّر الاسم
        if (
            st.session_state.selected_server
            and new_name != st.session_state.selected_server
            and st.session_state.selected_server in st.session_state.servers
        ):
            st.session_state.servers.pop(st.session_state.selected_server, None)
        st.session_state.servers[new_name.strip()] = new_url.strip()
        st.session_state.selected_server = new_name.strip()
        save_servers_to_disk(st.session_state.servers)
        st.success("Saved/Updated ✅")
    else:
        st.error("Please enter a valid name and URL.")

def _delete_selected_server():
    name = st.session_state.selected_server
    if name in st.session_state.servers:
        base = st.session_state.servers.pop(name)
        token_map = cast(Dict[str, Optional[str]], st.session_state["token_by_server"])
        token_map.pop(base, None)
        st.session_state.selected_server = next(iter(st.session_state.servers)) if st.session_state.servers else ""
        save_servers_to_disk(st.session_state.servers)
        st.success(f"Deleted '{name}'")

def _test_connection(base_url: str) -> tuple[bool, float, str]:
    """يرجع (ok, latency_ms, message). يجرب GET /health ثم /"""
    if not base_url:
        return (False, 0.0, "Empty URL")
    base = base_url.rstrip("/")
    start = time.perf_counter()
    try:
        for path in ("/health", "/"):
            try:
                r = requests.get(f"{base}{path}", timeout=5)
                dt = (time.perf_counter() - start) * 1000
                return (r.ok, dt, f"{path} → {r.status_code}")
            except requests.RequestException:
                continue
        dt = (time.perf_counter() - start) * 1000
        return (False, dt, "No reachable endpoint")
    except Exception as e:
        dt = (time.perf_counter() - start) * 1000
        return (False, dt, str(e))


# =========================
# OpenAPI Capability Detection
# =========================
@st.cache_data(ttl=60)
def fetch_openapi(base_url: str) -> Optional[dict]:
    """يحاول قراءة /openapi.json؛ يرجع None لو فشل."""
    if not base_url:
        return None
    base = base_url.rstrip("/")
    try:
        r = requests.get(f"{base}/openapi.json", timeout=6)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None

def _path_to_regex(template: str) -> re.Pattern:
    """حوّل /plugins/{name}/{task} إلى Regex يطابق /plugins/xxx/yyy"""
    esc = re.escape(template)
    pattern = re.sub(r"\\{[^/]+?\\}", r"[^/]+", esc)
    return re.compile("^" + pattern + "$")

def build_caps(openapi: Optional[dict]) -> Dict[str, set]:
    """يرجّع { 'GET': {paths...}, ... }"""
    caps: Dict[str, set] = {"GET": set(), "POST": set(), "PUT": set(), "DELETE": set(), "PATCH": set()}
    if not openapi or "paths" not in openapi:
        return caps
    for pth, item in openapi["paths"].items():
        for method in list(caps.keys()):
            if item.get(method.lower()):
                caps[method].add(pth)
    return caps

@st.cache_data(ttl=60)
def get_caps_for(base_url: str) -> Dict[str, set]:
    return build_caps(fetch_openapi(base_url))

def supports(base_url: str, method: str, path: str) -> bool:
    """يتحقق هل الـ endpoint مدعوم بناءً على OpenAPI (مع مطابقة المتغيّرات)."""
    caps = get_caps_for(base_url)
    method = method.upper()
    if method not in caps:
        return False
    wanted = "/" + path.strip("/")
    if wanted in caps[method]:
        return True
    for tpl in caps[method]:
        if "{" in tpl and "}" in tpl:
            if _path_to_regex(tpl).match(wanted):
                return True
    return False

def features_for(base_url: str) -> Dict[str, bool]:
    """يستنتج دعم التبويبات الأساسية لهذا السيرفر."""
    return {
        "auth":      supports(base_url, "POST", "/auth/login"),
        "auth_me":   supports(base_url, "GET",  "/auth/me"),
        "uploads":   supports(base_url, "GET",  "/uploads") or supports(base_url, "POST", "/uploads"),
        "plugins":   supports(base_url, "GET",  "/plugins") and supports(base_url, "POST", "/plugins/{name}/{task}"),
        "inference": supports(base_url, "POST", "/inference"),
        "workflows": supports(base_url, "GET",  "/workflows") or supports(base_url, "POST", "/workflows/run"),
        "root":      supports(base_url, "GET",  "/") or supports(base_url, "GET", "/docs") or supports(base_url, "GET", "/redoc"),
    }


# =========================
# Sidebar UI (Server Management)
# =========================
with st.sidebar:
    st.markdown("## ⚙️ Settings (Server Management)")

    server_names = list(st.session_state.servers.keys())
    has_servers = len(server_names) > 0

    # Select server (if any)
    st.markdown('<div class="ns-card">', unsafe_allow_html=True)
    if has_servers:
        if st.session_state.selected_server not in server_names:
            st.session_state.selected_server = server_names[0]
        st.session_state.selected_server = st.selectbox(
            "Select Server",
            server_names,
            index=server_names.index(st.session_state.selected_server),
            key="svr-select",
        )
    else:
        st.info("No servers yet. Add one below to get started.")
        st.session_state.selected_server = ""
    st.markdown('</div>', unsafe_allow_html=True)

    # Add / Update (FORM)
    default_name = st.session_state.selected_server or ""
    default_url = (
        st.session_state.servers.get(st.session_state.selected_server, "")
        if st.session_state.selected_server else ""
    )

    st.markdown('<div class="ns-card">', unsafe_allow_html=True)
    st.markdown("### ➕ Add / Update Server")

    with st.form("svr-form", clear_on_submit=False):
        colA, colB = st.columns(2)
        with colA:
            new_name = st.text_input(
                "Display Name", value=default_name, placeholder="e.g. Local :8000", key="svr-display-name"
            )
        with colB:
            new_url = st.text_input(
                "Base URL", value=default_url, placeholder="http://localhost:8000", key="svr-base-url"
            )

        c1, c2, c3, c4 = st.columns(4)
        save_clicked   = c1.form_submit_button("💾 Save / Update")
        test_clicked   = c2.form_submit_button("🧪 Test")
        del_clicked    = c3.form_submit_button("🗑️ Delete", disabled=not has_servers or not st.session_state.selected_server)
        reload_clicked = c4.form_submit_button("📥 Reload")

    if save_clicked:
        _save_update_server(new_name, new_url)

    if test_clicked:
        ok, ms, msg = _test_connection(new_url or default_url or "")
        state = "ok" if ok else "fail"
        st.markdown(
            f'<span class="ns-dot {state}"></span>'
            f'{"Reachable" if ok else "Unreachable"} '
            f'<span class="ns-latency">({ms:.0f} ms) · {msg}</span>',
            unsafe_allow_html=True,
        )

    if del_clicked:
        _delete_selected_server()

    if reload_clicked:
        st.session_state.servers = load_servers_from_disk()
        names = list(st.session_state.servers.keys())
        st.session_state.selected_server = names[0] if names else ""
        st.success("Reloaded from file.")

    st.markdown('</div>', unsafe_allow_html=True)

    # Token status
    st.markdown('<div class="ns-card">', unsafe_allow_html=True)
    if st.session_state.selected_server and st.session_state.selected_server in st.session_state.servers:
        current_base_sb = st.session_state.servers[st.session_state.selected_server]
        token_map = cast(Dict[str, Optional[str]], st.session_state["token_by_server"])
        if token_map.get(current_base_sb):
            st.markdown('<span class="ns-chip">🔐 Token exists for this server</span>', unsafe_allow_html=True)
            def _logout():
                token_map[current_base_sb] = None
                st.success("Token cleared.")
            st.button("Logout (Delete Token)", key="svr-logout", use_container_width=True, on_click=_logout)
        else:
            st.markdown('<span class="ns-chip">🔓 No token for this server yet.</span>', unsafe_allow_html=True)
    else:
        st.caption("Select or add a server to manage tokens.")
    st.markdown('</div>', unsafe_allow_html=True)

    # Capabilities badges
    if st.session_state.selected_server and st.session_state.selected_server in st.session_state.servers:
        base_for_badges = st.session_state.servers[st.session_state.selected_server]
        feats = features_for(base_for_badges)
        label_map = {
            "auth":"Auth", "uploads":"Uploads", "plugins":"Plugins",
            "inference":"Inference", "workflows":"Workflows", "root":"Health/Docs"
        }
        chips = []
        for k, lbl in label_map.items():
            ok = feats.get(k, False)
            dot = '<span class="ns-dot ok"></span>' if ok else '<span class="ns-dot fail"></span>'
            chips.append(f'<span class="ns-chip">{dot}{lbl}</span>')
        st.markdown('<div class="ns-card">' + " ".join(chips) + "</div>", unsafe_allow_html=True)

# Disable actions when no server selected
no_selection = (not st.session_state.selected_server) or (
    st.session_state.selected_server not in st.session_state.servers
)

current_base = st.session_state.servers.get(st.session_state.selected_server, "") if not no_selection else ""
current_feats = features_for(current_base) if current_base else {}


# =========================
# HTTP Helpers
# =========================
def api_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    require_auth: bool = False,
    base_url: Optional[str] = None,
) -> requests.Response:
    """Send a request to the selected or given server with optional token authentication."""
    if not base_url:
        if no_selection:
            st.error("No server selected. Add/select a server from the sidebar first.")
            raise RuntimeError("No server selected")
        base_url = st.session_state.servers[st.session_state.selected_server]

    base = base_url.rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    headers = {"Accept": "application/json"}

    token_map = cast(Dict[str, Optional[str]], st.session_state["token_by_server"])
    token = token_map.get(base)
    if require_auth and token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.request(
        method.upper(),
        url,
        params=params,
        json=json_body,
        files=files,
        headers=headers,
        timeout=60,
    )
    st.session_state.last_response = resp
    return resp

def show_response(resp: requests.Response) -> None:
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"**Status:** `{resp.status_code}`")
        try:
            st.json(resp.json())
        except Exception:
            st.code(resp.text or "<no body>")
    with col2:
        st.markdown("**Headers:**")
        try:
            st.code(json.dumps(dict(resp.headers), indent=2, ensure_ascii=False))
        except Exception:
            st.code(str(resp.headers))

def safe_json_input(
    label: str,
    default: dict | list | None = None,
    *,
    key: Optional[str] = None,
) -> Optional[dict | list]:
    default_text = json.dumps(default or {}, ensure_ascii=False, indent=2)
    txt = st.text_area(label, value=default_text, height=180, key=key)
    if not txt.strip():
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        st.error(f"Invalid JSON: {e}")
        return None


# =========================
# Main UI
# =========================
st.title("NeuroServe Dashboard (Multi-Server)")
st.caption(
    "Professional interface for managing multiple FastAPI servers: "
    "Add/Edit/Delete · Independent Tokens · Request Broadcasting"
)

tabs = st.tabs(["Auth", "Uploads", "Plugins", "Inference", "Workflows", "Health/Info", "Broadcast"])

# --- Auth ---
with tabs[0]:
    st.subheader("Login")
    auth_disabled = no_selection or not current_feats.get("auth", False)
    me_disabled   = no_selection or not current_feats.get("auth_me", False)

    c1, _ = st.columns(2)
    with c1:
        username = st.text_input("Username", value="", disabled=auth_disabled, key="auth-username")
        password = st.text_input("Password", value="", type="password", disabled=auth_disabled, key="auth-password")
        if st.button("POST /auth/login (selected server)", disabled=auth_disabled, key="auth-login"):
            body = {"username": username, "password": password}
            resp = api_request("POST", "/auth/login", json_body=body)
            if resp.ok:
                try:
                    data = resp.json()
                    token = data.get("access_token")
                    if token and not no_selection:
                        base = st.session_state.servers[st.session_state.selected_server]
                        token_map = cast(Dict[str, Optional[str]], st.session_state["token_by_server"])
                        token_map[base] = token
                        st.success("Login successful ✅")
                except Exception:
                    st.warning("Request succeeded but JSON parsing failed.")
            show_response(resp)

    if not current_feats.get("auth", True) and not no_selection:
        st.info("This server does not expose /auth/login")

    st.markdown("---")
    st.subheader("GET /auth/me (requires Token)")
    if st.button("GET /auth/me", disabled=me_disabled, key="auth-me"):
        resp = api_request("GET", "/auth/me", require_auth=True)
        show_response(resp)
    if not current_feats.get("auth_me", True) and not no_selection:
        st.info("This server does not expose /auth/me")

# --- Uploads ---
with tabs[1]:
    st.subheader("Uploads")
    uploads_disabled = no_selection or not current_feats.get("uploads", False)

    file = st.file_uploader("Choose a file", type=None, disabled=uploads_disabled, key="upl-file")
    if file is not None:
        files = {"file": (file.name, file.getvalue())}
        if st.button("POST /uploads", disabled=uploads_disabled, key="upl-post"):
            resp = api_request("POST", "/uploads", files=files)
            show_response(resp)

    st.markdown("---")
    if st.button("GET /uploads", disabled=uploads_disabled, key="upl-get"):
        resp = api_request("GET", "/uploads")
        show_response(resp)

    if not current_feats.get("uploads", True) and not no_selection:
        st.info("This server does not expose /uploads")

# --- Plugins ---
with tabs[2]:
    st.subheader("Plugins")
    plugins_disabled = no_selection or not current_feats.get("plugins", False)

    if st.button("GET /plugins", disabled=plugins_disabled, key="pl-list"):
        resp = api_request("GET", "/plugins")
        show_response(resp)

    st.markdown("---")
    st.subheader("Run Plugin Task")
    name = st.text_input("Plugin name", value="pdf_reader", disabled=plugins_disabled, key="pl-name")
    task = st.text_input("Task", value="extract_text", disabled=plugins_disabled, key="pl-task")
    payload = safe_json_input("Payload (JSON)", {"rel_path": "pdf/sample.pdf", "return_text": True}, key="pl-payload")
    if st.button("POST /plugins/{name}/{task}", disabled=plugins_disabled or payload is None, key="pl-run"):
        path = f"/plugins/{name}/{task}"
        resp = api_request("POST", path, json_body=payload or {})
        show_response(resp)

    if not current_feats.get("plugins", True) and not no_selection:
        st.info("This server does not expose /plugins endpoints")

# --- Inference ---
with tabs[3]:
    st.subheader("Inference (Unified)")
    inference_disabled = no_selection or not current_feats.get("inference", False)

    plugin = st.text_input("Plugin", value="pdf_reader", disabled=inference_disabled, key="inf-plugin")
    task = st.text_input("Task", value="extract_text", disabled=inference_disabled, key="inf-task")
    payload = safe_json_input("Payload (JSON)", {"rel_path": "pdf/sample.pdf", "return_text": True}, key="inf-payload")
    if st.button("POST /inference", disabled=inference_disabled or payload is None, key="inf-run"):
        body = {"plugin": plugin, "task": task, "payload": payload or {}}
        resp = api_request("POST", "/inference", json_body=body)
        show_response(resp)

    if not current_feats.get("inference", True) and not no_selection:
        st.info("This server does not expose /inference")

# --- Workflows ---
with tabs[4]:
    st.subheader("Workflows")
    wf_disabled = no_selection or not current_feats.get("workflows", False)

    wf_name = st.text_input("Workflow name", value="asr_clean_ar", disabled=wf_disabled, key="wf-name")
    wf_inputs = safe_json_input("Inputs (JSON)", {"text": "Hello", "lang": "ar"}, key="wf-inputs")
    if st.button("POST /workflows/run", disabled=wf_disabled or wf_inputs is None, key="wf-run"):
        body = {"name": wf_name, "inputs": wf_inputs or {}}
        resp = api_request("POST", "/workflows/run", json_body=body)
        show_response(resp)

    st.markdown("---")
    if st.button("GET /workflows", disabled=wf_disabled, key="wf-list"):
        resp = api_request("GET", "/workflows")
        show_response(resp)

    if not current_feats.get("workflows", True) and not no_selection:
        st.info("This server does not expose /workflows")

# --- Health/Info ---
with tabs[5]:
    st.subheader("Health / Docs")
    health_disabled = no_selection or not current_feats.get("root", False)

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("GET /", disabled=health_disabled, key="hi-root"):
            resp = api_request("GET", "/")
            show_response(resp)
    with c2:
        if st.button("GET /docs", disabled=health_disabled, key="hi-docs"):
            resp = api_request("GET", "/docs")
            st.write(resp.status_code)
            st.info("Open /docs in browser to view Swagger.")
    with c3:
        if st.button("GET /redoc", disabled=health_disabled, key="hi-redoc"):
            resp = api_request("GET", "/redoc")
            st.write(resp.status_code)
            st.info("Open /redoc in browser to view ReDoc.")

    if not current_feats.get("root", True) and not no_selection:
        st.info("This server does not expose root/docs endpoints")

    st.markdown("### Last Response (Debug)")
    if st.session_state.last_response is not None:
        show_response(st.session_state.last_response)
    else:
        st.caption("No response saved yet.")

# --- Broadcast to All Servers ---
with tabs[6]:
    st.subheader("Broadcast request to all servers")
    req_path = st.text_input("Path", value="/info", key="bc-path")
    method = st.selectbox("Method", ["GET", "POST", "PUT", "DELETE"], index=0, key="bc-method")
    body = safe_json_input("Body (JSON) — Optional", {}, key="bc-body")
    if st.button("Send to all", key="bc-send"):
        if not st.session_state.servers:
            st.warning("No servers to broadcast to.")
        else:
            for name, base in st.session_state.servers.items():
                st.write(f"**{name}** → {base}")
                # تخطّي السيرفرات غير الداعمة لهذا المسار/الميثود
                if not supports(base, method, req_path):
                    st.info("Skipped (unsupported endpoint on this server).")
                    continue
                try:
                    resp = api_request(method, req_path, json_body=(body or None), base_url=base)
                    st.code(f"Status: {resp.status_code}")
                    try:
                        st.json(resp.json())
                    except Exception:
                        st.text(resp.text)
                except Exception as e:
                    st.error(f"Failed: {e}")
