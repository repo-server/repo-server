# NeuroServe Dashboard (Multi-Server)

A lightweight framework to manage and run **multiple FastAPI servers** with a **Streamlit dashboard**, automatic orchestration via **run_all.py**, and built-in **health checks**.  
Supports multiple servers, independent tokens, and one-click broadcast requests.

> The dashboard (Streamlit) includes tabs: **Auth, Uploads, Plugins, Inference, Workflows, Health/Info, Broadcast**, with full server management from the sidebar (add/edit/delete/test) and persistence in `.streamlit/servers.json`.

---

## 🔗 Table of Contents
- [Features](#-features)
- [Architecture](#-architecture)
- [Quick Setup](#-quick-setup)
- [Running Services with run_all](#-running-services-with-run_all)
- [Streamlit Dashboard](#-streamlit-dashboard)
- [FastAPI Server](#-fastapi-server)
- [Plugins & Workflows](#-plugins--workflows)
- [Customization & Styling](#-customization--styling)
- [Screenshots](#-screenshots)
- [Roadmap](#-roadmap)
- [License](#-license)

---

## ✨ Features
- **Multi-service orchestration** (API + UI) with one command, including **health checks** before moving to the next service.  
- **Multi-server dashboard**: Add any number of FastAPI servers, test connectivity, save per-server tokens, and broadcast a request to all at once.  
- **Automatic capability detection** using **OpenAPI** to only show supported tabs/buttons.  
- Built-in **Auth (JWT demo)**, file uploads, plugin invocation, unified inference, and ready workflows.  
- **Dark, professional theme** via external CSS.  

---

## 🧱 Architecture
```
repo-server/
├─ fastapi/      # FastAPI server (APIs, Plugins, Workflows, Docs, CI, Tests)
├─ streamlit/    # Control dashboard (Streamlit) + .streamlit/servers.json
├─ run_all.py    # Service launcher + health checks + process management
└─ servers.json  # Service definitions (paths, commands, health URLs…)
```

---

## ⚡ Quick Setup

> Each service can use its own virtual environment (recommended), or you may share a single one. Define the correct `python_exe` path for each in `servers.json`.

### 1) Clone the repo
```bash
git clone https://github.com/repo-server/repo-server
cd repo-server
```

### 2) Create virtualenvs (recommended)
- **Inside fastapi/**
```bash
cd fastapi
python -m venv .venv
.venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

- **Inside streamlit/**
```bash
cd ../streamlit
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 🚀 Running Services with `run_all`

`run_all.py` launches services in order, performs **health checks** (waits for `200 OK`), prints “Healthy” or “Health timeout,” and opens the dashboard in the browser if successful. If any process exits, all are shut down gracefully.

### Example `servers.json`
```json
{
  "launch_order": ["api", "streamlit"],
  "services": {
    "api": {
      "cwd": "fastapi",
      "python_exe": "fastapi/.venv/Scripts/python.exe",
      "cmd": ["-m", "uvicorn", "main:app", "--app-dir", "app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
      "health": "http://127.0.0.1:8000/health",
      "health_timeout": 30.0
    },
    "streamlit": {
      "cwd": "streamlit",
      "python_exe": "streamlit/.venv/Scripts/python.exe",
      "cmd": ["-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", "8501"],
      "health": "http://127.0.0.1:8501/_stcore/health",
      "health_timeout": 30.0,
      "exports": { "url": "http://127.0.0.1:8501" }
    }
  }
}
```

### Start everything:
```bash
py -m run_all        # or: python run_all.py
```

### Start only one service:
```bash
py -m run_all api
```

---

## 🖥️ Streamlit Dashboard

- **Sidebar**: select server, add/edit/delete, **test** latency, reload servers list from disk.  
- **Tabs**:
  - **Auth** → login (`POST /auth/login`), verify with `/auth/me`.  
  - **Uploads** → upload/view via `/uploads`.  
  - **Plugins** → list plugins & run tasks `/plugins/{name}/{task}`.  
  - **Inference** → unified `POST /inference` API.  
  - **Workflows** → run/read workflows (`/workflows`, `/workflows/run`).  
  - **Health/Info** → open `/`, `/docs`, `/redoc`, plus latest response.  
  - **Broadcast** → send one request to all servers, skipping unsupported endpoints.  

---

## ⚙️ FastAPI Server

- Core endpoints: `/health` returning `{ "status": "ok" }`.  
- Routers: **Auth, Uploads, Plugins, Inference, Workflows, Services**.  
- Unified responses via `app/utils/unify.py`.  

Direct launch (without run_all):
```bash
cd fastapi
.venv\Scripts\activate
uvicorn main:app --app-dir app --reload --host 127.0.0.1 --port 8000
```

---

## 🔌 Plugins & Workflows

- **Plugins** in `app/plugins/<name>/` with `manifest.json` + `plugin.py`.  
- **Workflows** in `app/workflows/…` with `manifest.json` + `workflow.json`.  
- Use APIs:
  - `POST /plugins/{name}/{task}`
  - `POST /inference`
  - `POST /workflows/run`

---

## 🎨 Customization & Styling

Custom stylesheet: `streamlit/.streamlit/neuroserve.css` controls theme, cards, buttons, and badges.  

---

## 🖼️ Screenshots

### Dashboard – Plugins
<p align="center">
  <img src="docs/images/Screenshot.png" alt="Plugins Screenshot" width="800">
  <br>
  <em>Streamlit dashboard running the <code>pdf_reader</code> plugin (extract_text task).</em>
</p>


---

## 🗺️ Roadmap
- Real JWT authentication & user management.  
- Docker one-click launch.  
- CLI generator for Plugins & Workflows.  
- Extended CI integration tests.  

---

## 📜 License
This project is licensed under the [MIT License](LICENSE).

