#!/usr/bin/env python3
"""
Dynamic prefetch for all available plugins.

- Discovers plugins via app.plugins.loader
- For each plugin:
    1) Call instance.prefetch() if available  (can be disabled with --no-instance / --models-only)
    2) Prefetch models from Plugin.REQUIRED_MODELS / required_models()
    3) Prefetch models from manifest.json -> "models" (can be disabled with --no-manifest / --models-only)
- Supports:
    --only name1,name2    prefetch only specific plugins (by name or folder)
    --skip name1,name2    skip these plugins
    --dry-run             list actions without downloading
    --no-instance         do not call instance.prefetch()
    --no-manifest         ignore manifest models
    --models-only         same as --no-instance + --no-manifest = only REQUIRED_MODELS/required_models()
    --local-only          work from local cache only (HF local_files_only=True)
    --max-workers N       parallel downloads (1 = sequential)
- Idempotent; keeps a de-dup set to avoid fetching the same model twice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Ensure project root (fastapi/) in import path even if invoked directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins import loader  # noqa: E402

# Default model caches in project folder
HF_HOME_DEFAULT = ROOT / "models_cache" / "huggingface"
TORCH_HOME_DEFAULT = ROOT / "models_cache" / "torch"

# Environment defaults
os.environ.setdefault("HF_HOME", str(HF_HOME_DEFAULT))
os.environ.setdefault("TORCH_HOME", str(TORCH_HOME_DEFAULT))
HF_HOME_DEFAULT.mkdir(parents=True, exist_ok=True)
TORCH_HOME_DEFAULT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Optional .env
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass


# ============ utils ============

def info(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def warn(msg: str) -> None:
    print(f"[warn] {msg}")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ============ actual fetchers ============

# Keep a set of (type,id) to avoid duplicates across sources
_PROCESSED: set[tuple[str, str]] = set()


def _snapshot_hf(
    model_id: str,
    *,
    dry: bool = False,
    local_only: bool = False,
    retries: int = 3,
    delay: float = 1.0,
) -> None:
    """Download/config snapshot for HuggingFace models with simple retries."""
    if dry:
        print(f"  - would snapshot HF: {model_id}")
        return

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if not local_only:
                # Fast path: fetch config only (often triggers minimal pull)
                try:
                    from transformers import AutoConfig  # type: ignore
                    _ = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
                    return
                except Exception:
                    pass

            from huggingface_hub import snapshot_download  # type: ignore

            snapshot_download(
                repo_id=model_id,
                local_files_only=local_only,
                resume_download=not local_only,
            )
            return
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(delay * attempt)  # simple backoff
            else:
                warn(f"snapshot_download failed for {model_id} after {retries} attempts: {last_err}")


def _prefetch_torchvision(name: str, *, dry: bool = False) -> None:
    nm = name.lower()
    if nm == "resnet18":
        if dry:
            print("  - would prefetch torchvision resnet18 weights")
            return
        from torchvision.models import ResNet18_Weights, resnet18  # type: ignore

        _ = resnet18(weights=ResNet18_Weights.IMAGENET1K_V1)
    else:
        warn(f"unknown torchvision weight: {name}")


def _prefetch_entry(entry: dict[str, Any], *, dry: bool = False, local_only: bool = False) -> None:
    typ = (entry.get("type") or "").strip().lower()
    mid = (entry.get("id") or "").strip()
    if not mid:
        return
    key = (typ, mid)
    if key in _PROCESSED:
        return
    _PROCESSED.add(key)

    if typ in ("hf", "huggingface", "transformers"):
        info(f"Prefetch HF model: {mid}")
        _snapshot_hf(mid, dry=dry, local_only=local_only)
    elif typ in ("torchvision", "torch_hub"):
        info(f"Prefetch torchvision model: {mid}")
        _prefetch_torchvision(mid, dry=dry)
    else:
        info(f"Skip unknown model type: {entry}")


# ============ manifest helpers ============

def _collect_from_manifest(meta: dict[str, Any]) -> list[dict]:
    """Read models from plugin's manifest.json -> 'models'."""
    path = meta.get("manifest_file")
    out: list[dict] = []
    if not path:
        return out
    p = Path(path)
    if not p.exists():
        return out
    data = _read_json(p)
    models = data.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict) and m.get("id"):
                out.append(m)
    return out


# ============ main logic ============

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dynamic prefetch for all available plugins.")
    ap.add_argument("--only", help="Comma-separated plugin names to include (name or folder)")
    ap.add_argument("--skip", help="Comma-separated plugin names to skip")
    ap.add_argument("--dry-run", action="store_true", help="List actions without downloading")

    ap.add_argument("--no-instance", action="store_true", help="Do not call instance.prefetch()")
    ap.add_argument("--no-manifest", action="store_true", help="Ignore manifest 'models'")
    ap.add_argument("--models-only", action="store_true", help="Only REQUIRED_MODELS/required_models")

    ap.add_argument("--local-only", action="store_true", help="Use local cache only (no network)")
    ap.add_argument("--max-workers", type=int, default=1, help="Parallel downloads (1 = sequential)")
    args = ap.parse_args(argv)

    only = set((args.only or "").split(",")) - {""}
    skip = set((args.skip or "").split(",")) - {""}
    dry = bool(args.dry_run)

    no_instance = bool(args.no_instance or args.models_only)
    no_manifest = bool(args.no_manifest or args.models_only)
    local_only = bool(args.local_only)
    max_workers = int(args.max_workers or 1)

    print("HF_HOME =", os.getenv("HF_HOME"))
    print("TORCH_HOME =", os.getenv("TORCH_HOME"))
    print("Options:",
          f"dry_run={dry}",
          f"no_instance={no_instance}",
          f"no_manifest={no_manifest}",
          f"local_only={local_only}",
          f"max_workers={max_workers}",
          sep=" | ")

    # Discover plugins via the project loader
    loader.discover(reload=True)
    metas = loader.all_meta()  # list of dicts: {"name","folder","manifest_file","plugin_file", ...}

    jobs: list[dict[str, Any]] = []  # final model entries to prefetch

    for meta in metas:
        name = meta.get("name") or meta.get("folder") or "<unknown>"
        folder = meta.get("folder") or name
        if only and name not in only and folder not in only:
            continue
        if name in skip or folder in skip:
            continue

        info(f"Prefetch for plugin: {name}")

        # 1) instance.prefetch() (optional)
        inst = None
        if not no_instance:
            try:
                inst = loader.get(name)  # may import plugin
                pf = getattr(inst, "prefetch", None)
                if callable(pf):
                    if dry:
                        print("  - would call instance.prefetch()")
                    else:
                        pf()
            except Exception as e:
                print(f"  ! prefetch() not available or failed for {name}: {e}")
        else:
            # still try to get the instance to read REQUIRED_MODELS / required_models()
            try:
                inst = loader.get(name)
            except Exception:
                inst = None

        # 2) REQUIRED_MODELS / required_models()
        if inst:
            # static list
            try:
                req = list(getattr(inst, "REQUIRED_MODELS", []) or [])
            except Exception:
                req = []
            # dynamic
            try:
                fn = getattr(inst, "required_models", None)
                if callable(fn):
                    req += list(fn() or [])
            except Exception:
                pass

            for entry in req:
                jobs.append(entry)

        # 3) manifest "models" (optional)
        if not no_manifest:
            mf_models = _collect_from_manifest(meta)
            for entry in mf_models:
                jobs.append(entry)

    print(f"\nTotal planned entries: {len(jobs)} (before de-dup set)")

    # Execute downloads (de-dup happens inside _prefetch_entry)
    if max_workers > 1 and not dry:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_prefetch_entry, e, dry=dry, local_only=local_only) for e in jobs]
            for _ in as_completed(futs):
                pass
    else:
        for e in jobs:
            _prefetch_entry(e, dry=dry, local_only=local_only)

    print(f"\nSummary: processed={len(_PROCESSED)} unique model entries.")
    print("\nDone — dynamic prefetch finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
