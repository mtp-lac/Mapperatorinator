"""Inference worker supporting one-shot and resident model-caching modes."""

import gc
import json
import logging
import os
from pathlib import Path
import sys
import traceback


READY_MARKER = "MAPPERATORINATOR_READY"
RESULT_PREFIX = "MAPPERATORINATOR_RESULT "
DONE_PREFIX = "MAPPERATORINATOR_DONE "
UNLOADED_MARKER = "MAPPERATORINATOR_UNLOADED"


def _load_payload(request_path):
    request_path = Path(request_path)
    if not request_path.is_absolute():
        raise ValueError("Request path must be absolute")
    with request_path.open(encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or not isinstance(payload.get("overrides"), dict):
        raise ValueError("Request must contain model and overrides")
    return payload


def _compose_config(payload):
    from desktop.core import MODELS, ROOT

    model = payload.get("model")
    if model not in MODELS:
        raise ValueError("Unknown model")

    import config  # noqa: F401 - Registers structured inference configs with ConfigStore.
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    from osuT5.osuT5.event import ContextType

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
    return model, cfg


class ModelCache:
    """Keep the active model stack in memory across runs until switched or unloaded."""

    def __init__(self):
        self.main_key = None
        self.model = None
        self.tokenizer = None
        self.timing_key = None
        self.timing_model = None
        self.timing_tokenizer = None
        self.diff_key = None
        self.diff_model = None
        self.diff_tokenizer = None
        self.refine_model = None
        self._logger = None
        self._log_handler = None

    def _get_logger(self):
        if self._logger is None:
            logger = logging.getLogger("desktop.worker.inference")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            self._logger = logger
            self._log_handler = handler
        else:
            self._log_handler.setStream(sys.stdout)
        return self._logger

    @staticmethod
    def _path_key(value):
        if not value:
            return ""
        return value.as_posix() if isinstance(value, Path) else str(value)

    def _compute_main_key(self, cfg):
        from osuT5.osuT5.utils import (
            get_model_checkpoint_subfolder,
            resolve_compatible_lora_path,
            resolve_model_checkpoint_path,
        )

        resolved_ckpt, subfolder = resolve_model_checkpoint_path(
            cfg.model_path,
            gamemode=cfg.gamemode,
            auto_select_gamemode_model=cfg.auto_select_gamemode_model,
        )
        ckpt_subfolder = get_model_checkpoint_subfolder(resolved_ckpt, subfolder)
        effective_lora, _ = resolve_compatible_lora_path(
            cfg.lora_path,
            ckpt_subfolder=ckpt_subfolder,
            verbose=False,
        )
        return (
            self._path_key(resolved_ckpt),
            ckpt_subfolder or "",
            self._path_key(effective_lora),
            cfg.device,
            cfg.precision,
            cfg.attn_implementation,
        )

    def _compute_timing_key(self, cfg):
        from osuT5.osuT5.utils import get_model_checkpoint_subfolder, resolve_model_checkpoint_path

        resolved_ckpt, subfolder = resolve_model_checkpoint_path(
            cfg.model_path,
            gamemode=cfg.gamemode,
            auto_select_gamemode_model=False,
        )
        ckpt_subfolder = get_model_checkpoint_subfolder(resolved_ckpt, subfolder)
        return (
            self._path_key(resolved_ckpt),
            ckpt_subfolder or "",
            cfg.device,
            cfg.precision,
            cfg.attn_implementation,
        )

    @staticmethod
    def _compute_diff_key(cfg):
        return (
            str(cfg.diff_ckpt or ""),
            str(cfg.diff_refine_ckpt or ""),
            str(cfg.device),
            bool(cfg.compile),
        )

    @staticmethod
    def _clear_compiled_cache(model):
        if model is None:
            return
        try:
            from osuT5.osuT5.inference.compiled_decode import clear_decoder_cache
            clear_decoder_cache(model)
        except Exception:
            pass

    @staticmethod
    def _release_device_memory():
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _unload_timing(self):
        if self.timing_model is None and self.timing_tokenizer is None:
            return False
        self._clear_compiled_cache(self.timing_model)
        self.timing_key = None
        self.timing_model = None
        self.timing_tokenizer = None
        return True

    def _unload_diffusion(self):
        if self.diff_model is None and self.diff_tokenizer is None and self.refine_model is None:
            return False
        self.diff_key = None
        self.diff_model = None
        self.diff_tokenizer = None
        self.refine_model = None
        return True

    def unload(self, emit_marker=True):
        """Release all cached models and free device memory."""
        had_model = any(
            item is not None
            for item in (
                self.model, self.tokenizer,
                self.timing_model, self.timing_tokenizer,
                self.diff_model, self.diff_tokenizer, self.refine_model,
            )
        )
        self._clear_compiled_cache(self.model)
        self.main_key = None
        self.model = None
        self.tokenizer = None
        self._unload_timing()
        self._unload_diffusion()
        if had_model:
            self._release_device_memory()
            print("Unloaded model from memory.", flush=True)
            if emit_marker:
                print(UNLOADED_MARKER, flush=True)
        return had_model

    def run_request(self, payload):
        from desktop.core import MODELS
        import inference

        model_name, cfg = _compose_config(payload)
        inference.compile_args(cfg)
        inference.setup_inference_environment(cfg.seed)

        main_key = self._compute_main_key(cfg)
        if self.model is not None and self.main_key != main_key:
            print("Switching model — unloading previous model...", flush=True)
            self.unload(emit_marker=False)

        if self.model is None:
            print(f"Loading {MODELS[model_name]}...", flush=True)
            self.model, self.tokenizer = inference.load_model_with_server(
                cfg.model_path,
                cfg.train,
                cfg.device,
                max_batch_size=cfg.max_batch_size,
                use_server=False,
                precision=cfg.precision,
                attn_implementation=cfg.attn_implementation,
                lora_path=cfg.lora_path,
                gamemode=cfg.gamemode,
                auto_select_gamemode_model=cfg.auto_select_gamemode_model,
                fast_decoder_loop=cfg.fast_decoder_loop,
            )
            self.main_key = main_key
        else:
            print(f"Reusing loaded {MODELS[model_name]} in memory...", flush=True)

        needs_timing = inference.should_load_separate_timing_model(cfg)
        desired_timing_key = self._compute_timing_key(cfg) if needs_timing else None
        if self.timing_model is not None and self.timing_key != desired_timing_key:
            if self._unload_timing():
                self._release_device_memory()
        if needs_timing and self.timing_model is None:
            print("Using base model for timing generation.", flush=True)
            self.timing_model, self.timing_tokenizer = inference.load_model_with_server(
                cfg.model_path,
                cfg.train,
                cfg.device,
                max_batch_size=cfg.max_batch_size,
                use_server=False,
                precision=cfg.precision,
                attn_implementation=cfg.attn_implementation,
                gamemode=cfg.gamemode,
                auto_select_gamemode_model=False,
                fast_decoder_loop=cfg.fast_decoder_loop,
            )
            self.timing_key = desired_timing_key

        needs_diff = bool(cfg.generate_positions)
        desired_diff_key = self._compute_diff_key(cfg) if needs_diff else None
        if (self.diff_model is not None or self.refine_model is not None) and self.diff_key != desired_diff_key:
            if self._unload_diffusion():
                self._release_device_memory()
        if needs_diff and self.diff_model is None:
            diff_model, diff_tokenizer = inference.load_diff_model(cfg.diff_ckpt, cfg.diffusion, cfg.device)
            refine_model = None
            if os.path.exists(cfg.diff_refine_ckpt):
                refine_model = inference.load_diff_model(cfg.diff_refine_ckpt, cfg.diffusion, cfg.device)[0]
            if cfg.compile:
                import torch
                diff_model.forward = torch.compile(diff_model.forward, mode="reduce-overhead", fullgraph=True)
            self.diff_model = diff_model
            self.diff_tokenizer = diff_tokenizer
            self.refine_model = refine_model
            self.diff_key = desired_diff_key

        generation_config, beatmap_config = inference.get_config(cfg)
        print("Starting inference...", flush=True)
        return inference.generate(
            cfg,
            generation_config=generation_config,
            beatmap_path=cfg.beatmap_path,
            beatmap_config=beatmap_config,
            model=self.model,
            tokenizer=self.tokenizer,
            timing_model=self.timing_model,
            timing_tokenizer=self.timing_tokenizer,
            diff_model=self.diff_model,
            diff_tokenizer=self.diff_tokenizer,
            refine_model=self.refine_model,
            logger=self._get_logger(),
        )


def serve(stdin=None, cache=None):
    """Run a resident worker loop reading JSON commands from stdin."""
    if os.name == "posix" and os.getsid(0) != os.getpid():
        os.setsid()
    stream = sys.stdin if stdin is None else stdin
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cache = ModelCache() if cache is None else cache
    print(READY_MARKER, flush=True)
    try:
        while True:
            raw_line = stream.readline()
            if not raw_line:
                break
            line = raw_line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Command must be a JSON object")
                action = command.get("cmd")
                if action == "quit":
                    cache.unload()
                    break
                if action == "unload":
                    cache.unload()
                    continue
                if action == "run":
                    payload = _load_payload(command.get("request_path", ""))
                    result = cache.run_request(payload)
                    print(
                        "\n" + RESULT_PREFIX + json.dumps({"path": str(result[1])}, ensure_ascii=False),
                        flush=True,
                    )
                    print(
                        DONE_PREFIX + json.dumps(
                            {"status": "completed", "message": "Generation complete."},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    continue
                raise ValueError(f"Unknown worker command: {action}")
            except Exception as exc:
                print("Inference failed:", file=sys.stderr, flush=True)
                traceback.print_exc()
                sys.stderr.flush()
                sys.stdout.flush()
                detail = str(exc).strip()
                message = f"Generation failed: {detail}" if detail else "Generation failed. See the log for details."
                print(
                    "\n" + DONE_PREFIX + json.dumps({"status": "failed", "message": message}, ensure_ascii=False),
                    flush=True,
                )
    finally:
        cache.unload()
    return 0


def main(argv=None):
    """Load heavy dependencies only in the inference subprocess."""
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--serve"]:
        return serve()
    try:
        if os.name == "posix" and os.getsid(0) != os.getpid():
            os.setsid()
        if len(argv) != 1:
            raise ValueError("Usage: python -u -m desktop.worker /absolute/request.json")
        payload = _load_payload(argv[0])
        from desktop.core import MODELS
        from inference import main as infer

        model, cfg = _compose_config(payload)
        print(f"Loading {MODELS[model]}...", flush=True)
        print("Starting inference...", flush=True)
        result = infer.__wrapped__(cfg)
        print("\n" + RESULT_PREFIX + json.dumps({"path": str(result[1])}, ensure_ascii=False), flush=True)
        return 0
    except Exception:
        print("Inference failed:", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
