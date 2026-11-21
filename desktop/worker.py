"""One-shot inference subprocess: python -u -m desktop.worker REQUEST.json."""

import json
import os
from pathlib import Path
import sys
import traceback


def main(argv=None):
    """Load heavy dependencies only in the inference subprocess."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if os.name == "posix" and os.getsid(0) != os.getpid():
            os.setsid()
        if len(argv) != 1:
            raise ValueError("Usage: python -u -m desktop.worker /absolute/request.json")
        request_path = Path(argv[0])
        if not request_path.is_absolute():
            raise ValueError("Request path must be absolute")
        with request_path.open(encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or not isinstance(payload.get("overrides"), dict):
            raise ValueError("Request must contain model and overrides")

        from desktop.core import MODELS, ROOT

        model = payload.get("model")
        if model not in MODELS:
            raise ValueError("Unknown model")
        print(f"Loading {MODELS[model]}...", flush=True)
        import config  # Registers structured inference configs with ConfigStore.
        from hydra import compose, initialize_config_dir
        from omegaconf import OmegaConf
        from osuT5.osuT5.event import ContextType
        from inference import main as infer

        with initialize_config_dir(version_base="1.1", config_dir=str(ROOT / "configs" / "inference")):
            cfg = OmegaConf.to_object(compose(config_name=model))
        for key, value in payload["overrides"].items():
            if not hasattr(cfg, key) or key in {"train", "diffusion", "hydra"}:
                raise ValueError(f"Unknown or protected inference setting: {key}")
            if key in {"in_context", "output_type"}:
                if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
                    raise ValueError(f"{key} must be a list of context names")
                value = [ContextType[name] for name in value]
            setattr(cfg, key, value)
        cfg.use_server = False
        print("Starting inference...", flush=True)
        result = infer.__wrapped__(cfg)
        print("\nMAPPERATORINATOR_RESULT " + json.dumps({"path": str(result[1])}, ensure_ascii=False), flush=True)
        return 0
    except Exception:
        print("Inference failed:", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
