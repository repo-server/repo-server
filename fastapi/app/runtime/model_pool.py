# app/runtime/model_pool.py
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import torch

from app.core.config import get_settings


class ModelPool:
    """
    Keeps up to `max_active` models loaded (e.g., on GPU).
    Evicts least-recently-used models and unloads idle ones.
    Thread-safe.
    """

    def __init__(self, max_active: int = 2, idle_unload_s: int = 600):
        self.max_active = max_active
        self.idle_unload_s = idle_unload_s
        self.pool: OrderedDict[str, dict] = OrderedDict()  # name -> {"model": obj, "last": ts}
        self.lock = threading.Lock()

    def get(self, name: str, factory: Callable[[], Any]):
        """
        Get (or lazily create) a model by name.
        `factory` must return a ready-to-use model (moved to device, eval(), dtype set, etc.).
        """
        now = time.time()
        with self.lock:
            if name in self.pool:
                self.pool[name]["last"] = now
                self.pool.move_to_end(name)
                return self.pool[name]["model"]

            # Create/load new model
            model = factory()
            self.pool[name] = {"model": model, "last": now}
            self.pool.move_to_end(name)

            # Evict LRU while exceeding max_active
            while self.max_active > 0 and len(self.pool) > self.max_active:
                old_key, item = self.pool.popitem(last=False)
                self._safe_del(item.get("model"))

            self._empty_cuda_cache()
            return model

    def sweep_idle(self):
        """
        Unload models that have been idle longer than `idle_unload_s`.
        """
        if self.idle_unload_s <= 0:
            return
        now = time.time()
        with self.lock:
            to_drop = [k for k, v in self.pool.items() if now - v["last"] > self.idle_unload_s]
            for k in to_drop:
                item = self.pool.pop(k)
                self._safe_del(item.get("model"))
        self._empty_cuda_cache()

    @staticmethod
    def _safe_del(model):
        try:
            del model
        except Exception:
            pass

    @staticmethod
    def _empty_cuda_cache():
        if torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass


@lru_cache
def get_model_pool() -> ModelPool:
    """
    Global singleton pool configured from Settings.
    Safe to import from plugins without circular imports.
    """
    s = get_settings()
    return ModelPool(
        max_active=s.MAX_ACTIVE_MODELS,
        idle_unload_s=s.IDLE_UNLOAD_SECONDS,
    )

if __name__ == "__main__":
    """
    CLI بسيطة لاختبار ModelPool بدون تحميل نماذج ثقيلة.
    أمثلة تشغيل:
      - عرض الحالة فقط:
          python app/runtime/model_pool.py --status
      - إنشاء نماذج وهمية وتحديث LRU:
          python app/runtime/model_pool.py --demo
      - اختبار تفريغ الخامل (sweep):
          python app/runtime/model_pool.py --demo --sleep 2 --sweep 1
    """
    import argparse
    import time

    parser = argparse.ArgumentParser(description="ModelPool utility (no heavy models).")
    parser.add_argument("--status", action="store_true", help="اعرض حالة الـ pool الحالية")
    parser.add_argument("--demo", action="store_true", help="حمّل نماذج وهمية لإظهار LRU")
    parser.add_argument("--count", type=int, default=3, help="عدد النماذج الوهمية في الديمو")
    parser.add_argument("--sleep", type=float, default=0.0, help="انتظار بين التحميلات (ثوانٍ)")
    parser.add_argument("--sweep", type=int, default=0, help="نفّذ sweep بعد N ثوانٍ (0 = لا)")
    args = parser.parse_args()

    pool = get_model_pool()

    def dummy_factory(idx: int):
        # كائن خفيف يمثل "موديل" بدون أي مكتبات ثقيلة
        class DummyModel:
            def __init__(self, name: str):
                self.name = name
            def __repr__(self) -> str:
                return f"<DummyModel {self.name}>"
        return DummyModel(f"m{idx}")

    if args.status and not args.demo:
        # عرض قائمة العناصر وبصمة زمن آخر استخدام
        with pool.lock:
            print(f"max_active={pool.max_active}, idle_unload_s={pool.idle_unload_s}")
            print(f"pool_size={len(pool.pool)}")
            for k, v in pool.pool.items():
                age = time.time() - v["last"]
                print(f"- {k}: last_used={v['last']:.0f} (age {age:.1f}s), obj={v['model']}")
        raise SystemExit(0)

    if args.demo:
        print(f"💡 Demo: إنشاء {args.count} نموذج(اً) وهمياً...")
        for i in range(args.count):
            name = f"demo_{i+1}"
            obj = pool.get(name, lambda i=i: dummy_factory(i))
            print(f"  loaded: {name} -> {obj}")
            if args.sleep > 0:
                time.sleep(args.sleep)

        with pool.lock:
            print("\n📦 المحتويات بعد التحميل:")
            for k, v in pool.pool.items():
                print(f"- {k}: obj={v['model']}")

        if args.sweep > 0:
            print(f"\n⏳ الانتظار {args.sweep}s ثم sweep_idle() ...")
            time.sleep(args.sweep)
            pool.sweep_idle()

            with pool.lock:
                print("\n🧹 بعد الـ sweep:")
                for k, v in pool.pool.items():
                    print(f"- {k}: obj={v['model']}")

        print("\n✅ تم.")
    else:
        # الوضع الافتراضي: طباعة مساعدة
        parser.print_help()
