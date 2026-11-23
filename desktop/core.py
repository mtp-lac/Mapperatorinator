"""Lightweight settings and validation shared by the native desktop UI."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path, PureWindowsPath
import re

ROOT = Path(__file__).resolve().parent.parent
MODELS = {
    "v28": "Mapperatorinator V28",
    "v29": "Mapperatorinator V29 (Supports gamemodes and descriptors)",
    "v30": "Mapperatorinator V30 (Previous best model)",
    "v31": "Mapperatorinator V31 (Slightly more accurate than V29)",
    "v32-mini": "Mapperatorinator V32-mini",
    "v32": "Mapperatorinator V32 (Best model)",
}
DEFAULTS = {
    "model": "v32", "gamemode": 0, "difficulty": "5", "year": "2023",
    "audio_path": "", "beatmap_path": "", "output_path": "", "lora_path": "",
    "background_image": "", "title": "", "title_unicode": "", "artist": "",
    "artist_unicode": "", "creator": "", "version": "", "source": "", "tags": "",
    "hp_drain_rate": "5", "circle_size": "4", "keycount": "4",
    "overall_difficulty": "8", "approach_rate": "9", "slider_multiplier": "1.4",
    "slider_tick_rate": "1", "hold_note_ratio": "", "scroll_speed_ratio": "",
    "cfg_scale": "1.0", "temperature": "0.9", "top_p": "0.9",
    "seed": "", "mapper_id": "", "preview_time": "", "start_time": "", "end_time": "",
    "hitsounded": True, "export_osz": False, "super_timing": False,
    "add_to_beatmap": False, "overwrite_reference_beatmap": False,
    "precision": "fp32", "device": "auto", "descriptors": [],
    "fast_decoder_loop": False, "super_timing_fast_loop": False,
    "negative_descriptors": [], "in_context": [], "output_type": None,
}

# Limits mirror the web form; scroll-speed conditioning is supported by the
# inference engine for taiko and mania (the web template incorrectly says catch).
_NUMBERS = {
    "difficulty": (float, 0, None), "hp_drain_rate": (float, 0, 10),
    "circle_size": (float, 0, 10), "keycount": (int, 1, 18),
    "overall_difficulty": (float, 0, 10), "approach_rate": (float, 0, 10),
    "slider_multiplier": (float, 0, 3.6), "slider_tick_rate": (float, 0.5, 4),
    "hold_note_ratio": (float, 0, 1), "scroll_speed_ratio": (float, 0, 1),
    "cfg_scale": (float, 0, None), "temperature": (float, 0, 1),
    "top_p": (float, 0, 1), "seed": (int, 0, 2**32 - 1),
    "mapper_id": (int, 0, None), "preview_time": (int, -1, 600000),
    "start_time": (int, 0, None), "end_time": (int, 0, None),
}
_TEXT = ("title", "title_unicode", "artist", "artist_unicode", "creator", "version", "source", "tags")
_BOOLEANS = ("hitsounded", "export_osz", "super_timing", "add_to_beatmap",
             "overwrite_reference_beatmap", "fast_decoder_loop", "super_timing_fast_loop")
_CONTEXTS = {"TIMING", "KIAI", "MAP", "GD", "NO_HS"}
_ENUM_NAMES = _CONTEXTS | {"NONE", "SV"}


def _bool(value, key):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("true", "1", "yes", "on"):
            return True
        if value in ("false", "0", "no", "off", ""):
            return False
    raise ValueError(f"{key} must be a boolean")


def load_settings(data: dict) -> dict:
    """Merge known desktop fields or a web export into fresh form defaults."""
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object")
    source = data.get("settings", data)
    if not isinstance(source, dict):
        raise ValueError("settings must be an object")
    result = deepcopy(DEFAULTS)
    result.update({key: deepcopy(value) for key, value in source.items() if key in DEFAULTS})
    if "precision" not in source and "enable_bf16" in source:
        result["precision"] = "bf16" if _bool(source["enable_bf16"], "enable_bf16") else "fp32"
    if "in_context" not in source and "in_context_options" in source:
        value = source["in_context_options"]
        result["in_context"] = deepcopy(value if isinstance(value, list) else [value])
    if "settings" in data:
        descriptors = data.get("descriptors")
        if descriptors is not None:
            if not isinstance(descriptors, dict):
                raise ValueError("descriptors must contain positive and negative lists")
            result["descriptors"] = deepcopy(descriptors.get("positive", []))
            result["negative_descriptors"] = deepcopy(descriptors.get("negative", []))
        if "inContextOptions" in data:
            result["in_context"] = deepcopy(data["inContextOptions"])
    return result


def export_settings(settings: dict) -> dict:
    """Return a web-compatible envelope, retaining desktop-only fields."""
    settings = load_settings(settings)
    positive = settings.pop("descriptors")
    negative = settings.pop("negative_descriptors")
    context = settings.pop("in_context")
    settings["enable_bf16"] = settings["precision"] == "bf16"
    return {
        "version": "1.0", "settings": settings,
        "descriptors": {"positive": positive, "negative": negative},
        "inContextOptions": context,
    }


def _text(value, key):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value.strip() or None


def _number(value, key, kind, minimum, maximum):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value).strip())
        if not number.is_finite() or (kind is int and number != number.to_integral_value()):
            raise ValueError
        if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
            raise ValueError
        result = kind(number)
        if kind is float and not math.isfinite(result):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        bounds = f"{minimum if minimum is not None else '-infinity'} to {maximum if maximum is not None else 'infinity'}"
        raise ValueError(f"{key} must be a finite {kind.__name__} in range {bounds}") from None


def _path(value, key):
    text = _text(value, key)
    if text is None:
        return None
    if "\x00" in text:
        raise ValueError(f"{key} contains a null character")
    path = Path(text).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def _list(value, key):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must be a list of nonempty strings")
    return [item.strip() for item in value]


def _reference_audio(parent, filename):
    # osu filenames may be quoted or use Windows separators on any platform.
    if len(filename) >= 2 and filename[0] == filename[-1] == '"':
        filename = filename[1:-1]
    name = Path(filename.replace("\\", "/"))
    if not filename or name.is_absolute() or PureWindowsPath(filename).drive or ".." in name.parts or "\x00" in filename:
        raise ValueError("Reference AudioFilename must be a relative path inside its beatmap folder")
    path = (parent / name).resolve()
    if not path.is_relative_to(parent.resolve()):
        raise ValueError("Reference AudioFilename escapes its beatmap folder")
    return str(path)


def read_reference(path) -> dict:
    """Read .osu metadata without importing the model or executing file contents."""
    path = _path(str(path), "beatmap_path")
    if path is None or path.suffix.lower() != ".osu" or not path.is_file():
        raise ValueError("beatmap_path must point to an existing .osu file")
    sections = {}
    section = None
    with path.open(encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section in {"General", "Metadata", "Difficulty"} and ":" in line:
                key, value = line.split(":", 1)
                sections.setdefault(section, {})[key.strip()] = value.strip()
    general = sections.get("General", {})
    result = {"beatmap_path": str(path), "output_path": str(path.parent), "audio_path": None}
    if general.get("AudioFilename"):
        result["audio_path"] = _reference_audio(path.parent, general["AudioFilename"])
    metadata = sections.get("Metadata", {})
    for source, target in {
        "Title": "title", "TitleUnicode": "title_unicode", "Artist": "artist",
        "ArtistUnicode": "artist_unicode", "Creator": "creator", "Version": "version",
        "Source": "source", "Tags": "tags",
    }.items():
        if metadata.get(source):
            result[target] = metadata[source]
    for values, mapping in (
        (general, {"Mode": ("gamemode", int), "PreviewTime": ("preview_time", int)}),
        (metadata, {"BeatmapID": ("beatmap_id", int), "BeatmapSetID": ("beatmap_set_id", int)}),
        (sections.get("Difficulty", {}), {
            "HPDrainRate": ("hp_drain_rate", float), "CircleSize": ("circle_size", float),
            "OverallDifficulty": ("overall_difficulty", float), "ApproachRate": ("approach_rate", float),
            "SliderMultiplier": ("slider_multiplier", float), "SliderTickRate": ("slider_tick_rate", float),
        }),
    ):
        for source, (target, kind) in mapping.items():
            if source in values:
                try:
                    result[target] = _number(values[source], target, kind, None, None)
                except ValueError:
                    pass  # Malformed optional metadata should not prevent path discovery.
    if result.get("gamemode") == 3 and "circle_size" in result:
        result["keycount"] = result["circle_size"]
    return result


def validate_settings(settings: dict, check_paths=True) -> dict:
    """Return typed inference overrides, or raise ValueError for invalid input.

    check_paths=False skips filesystem checks and reference parsing, useful for
    editing settings on machines where the selected media is unavailable.
    """
    settings = load_settings(settings)
    model = settings["model"]
    if model not in MODELS:
        raise ValueError("Unknown model")
    mode = _number(settings["gamemode"], "gamemode", int, 0, 3)
    if mode is None or (model == "v30" and mode != 0):
        raise ValueError("v30 supports only gamemode 0" if model == "v30" else "gamemode is required")
    result = {"gamemode": mode, "use_server": False}
    for key in _BOOLEANS:
        if key != "hitsounded" or model != "v30":
            result[key] = _bool(settings[key], key)
    for key, spec in _NUMBERS.items():
        if key in {"keycount", "hold_note_ratio"} and mode != 3:
            continue
        if key == "circle_size" and mode != 0:
            continue
        if key == "scroll_speed_ratio" and mode not in {1, 3}:
            continue
        result[key] = _number(settings[key], key, *spec)
    for key in ("cfg_scale", "temperature", "top_p"):
        if result[key] is None:
            result.pop(key)  # Leave model-specific sampling defaults intact.
    if model != "v30":
        result["year"] = _number(settings["year"], "year", int, 2007, 2024 if model in {"v32", "v32-mini"} else 2023)
        available = {item["value"] for item in descriptor_catalog(model, mode)}
        for key in ("descriptors", "negative_descriptors"):
            values = _list(settings[key], key)
            unsupported = [value for value in values if value not in available]
            if unsupported:
                raise ValueError(f"{key} unavailable for {model}, gamemode {mode}: {', '.join(unsupported)}")
            result[key] = list(dict.fromkeys(values)) or None
        if (result.get("cfg_scale", 1) > 1 and result["negative_descriptors"]
                and len(result["negative_descriptors"]) != len(result["descriptors"] or [])):
            raise ValueError("negative_descriptors must match the positive descriptor count when cfg_scale > 1")
    start, end = result["start_time"], result["end_time"]
    if end is not None and end <= (start if start is not None else 0):
        raise ValueError("end_time must be greater than start_time")
    for key in _TEXT:
        result[key] = _text(settings[key], key)
    precision = _text(settings["precision"], "precision")
    if precision not in {"auto", "fp32", "fp16", "bf16", "amp"}:
        raise ValueError("precision must be auto, fp32, fp16, bf16, or amp")
    if precision == "auto":
        precision = "bf16" if check_bf16_support()["supported"] else "fp32"
    device = _text(settings["device"], "device")
    if device is None or not re.fullmatch(r"auto|cpu|mps|cuda(?::\d+)?", device):
        raise ValueError("device must be auto, cpu, mps, cuda, or cuda:<index>")
    result.update(precision=precision, device=device)
    context = [name.upper() for name in _list(settings["in_context"], "in_context")]
    if context == ["NONE"]:
        context = []
    allowed = {"TIMING"} if model in {"v30", "v32", "v32-mini"} else _CONTEXTS
    if any(name not in allowed for name in context):
        raise ValueError(f"Unsupported in_context for {model}; allowed: {', '.join(sorted(allowed))}")
    result["in_context"] = list(dict.fromkeys(context))
    if settings["output_type"] is not None:
        output = [name.upper() for name in _list(settings["output_type"], "output_type")]
        if not output or any(name not in _ENUM_NAMES - {"NONE"} for name in output):
            raise ValueError("output_type must contain valid context names")
        result["output_type"] = output
    paths = {key: _path(settings[key], key) for key in ("audio_path", "beatmap_path", "output_path", "lora_path", "background_image")}
    reference = paths["beatmap_path"]
    if (context or result["add_to_beatmap"] or result["overwrite_reference_beatmap"]) and reference is None:
        raise ValueError("Context, add_to_beatmap, and overwrite_reference_beatmap require a reference beatmap")
    if result["overwrite_reference_beatmap"] and not result["add_to_beatmap"]:
        raise ValueError("overwrite_reference_beatmap requires add_to_beatmap")
    if reference is not None:
        if reference.suffix.lower() != ".osu":
            raise ValueError("beatmap_path must have .osu extension")
        if check_paths:
            info = read_reference(reference)
            if paths["audio_path"] is None and info["audio_path"]:
                paths["audio_path"] = Path(info["audio_path"])
            if (result["add_to_beatmap"] or any(name in {"MAP", "GD", "NO_HS"} for name in context)) and info.get("gamemode", 0) != mode:
                raise ValueError("Reference beatmap gamemode must match the selected gamemode")
    if paths["audio_path"] is None and (reference is None or check_paths):
        raise ValueError("An audio file or reference beatmap with AudioFilename is required")
    if paths["audio_path"] is not None and paths["audio_path"].suffix.lower() not in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}:
        raise ValueError("audio_path must have .mp3, .wav, .ogg, .m4a, or .flac extension")
    if paths["output_path"] is None:
        paths["output_path"] = reference.parent if reference else paths["audio_path"].parent
    if check_paths:
        for key in ("audio_path", "background_image"):
            if paths[key] is not None and not paths[key].is_file():
                raise ValueError(f"{key} must point to an existing file")
        if paths["lora_path"] is not None and not (paths["lora_path"].is_file() or paths["lora_path"].is_dir()):
            raise ValueError("lora_path must point to an existing file or folder")
        output = paths["output_path"]
        if output.exists() and not output.is_dir():
            raise ValueError("output_path must be a directory")
        if any(parent.exists() and not parent.is_dir() for parent in output.parents):
            raise ValueError("output_path has a parent that is not a directory")
    for key, path in paths.items():
        result["background" if key == "background_image" else key] = str(path) if path is not None else None
    return result


def descriptor_catalog(model, gamemode=None) -> list[dict]:
    """Load descriptors in the same group order and ruleset scope as the Web UI."""
    if model not in MODELS:
        raise ValueError("Unknown model")
    if model == "v30":
        return []
    filename = "tags_2026.json" if model in {"v32", "v32-mini"} else "omdb_descriptors.json"
    try:
        with (ROOT / "datasets" / filename).open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("tags"), list):
        return []
    groups = {}
    for tag in data["tags"]:
        if not isinstance(tag, dict) or not isinstance(tag.get("name"), str):
            continue
        name = tag["name"].strip()
        value = tag.get("value") or name
        if not name or not isinstance(value, str) or not value.strip():
            continue
        ruleset = tag.get("ruleset_id")
        if gamemode is not None and ruleset is not None and str(ruleset) != str(gamemode):
            continue
        group, label = name.split("/", 1) if "/" in name else ("other", name)
        title = " ".join(part.capitalize() for part in group.replace("_", " ").split())
        groups.setdefault(group, []).append({
            "value": value.strip(), "label": label, "group": group, "group_title": title,
            "description": tag.get("description") or "", "ruleset_id": ruleset,
        })
    return [descriptor for items in groups.values() for descriptor in items]


_BF16_CACHE = None
_COMPUTE_CACHE = None


def check_bf16_support(use_cache=True) -> dict:
    """Test whether CUDA BF16 precision is natively supported, caching the result by default."""
    global _BF16_CACHE
    if use_cache and _BF16_CACHE is not None:
        return dict(_BF16_CACHE)

    try:
        import torch
    except Exception:
        result = {"supported": False, "gpu_name": "", "compute_capability": "", "reason": "PyTorch not available"}
        _BF16_CACHE = dict(result)
        return dict(result)

    try:
        if not torch.cuda.is_available():
            result = {"supported": False, "gpu_name": "", "compute_capability": "", "reason": "CUDA not available"}
            _BF16_CACHE = dict(result)
            return dict(result)

        gpu_name = ""
        compute_capability = ""
        try:
            props = torch.cuda.get_device_properties(0)
            gpu_name = getattr(props, "name", "") or ""
            major = getattr(props, "major", None)
            minor = getattr(props, "minor", None)
            if major is not None and minor is not None:
                compute_capability = f"{major}.{minor}"
        except Exception:
            pass

        if hasattr(torch.cuda, "is_bf16_supported"):
            try:
                supported = bool(torch.cuda.is_bf16_supported(including_emulation=False))
            except TypeError:
                supported = bool(torch.cuda.is_bf16_supported())
        elif compute_capability:
            supported = int(compute_capability.split(".", 1)[0]) >= 8
        else:
            supported = False

        if supported:
            reason = "GPU supports bf16"
        elif compute_capability:
            reason = f"GPU compute capability {compute_capability} does not natively support bf16"
        else:
            reason = "GPU does not natively support bf16"
        result = {
            "supported": supported,
            "gpu_name": gpu_name,
            "compute_capability": compute_capability,
            "reason": reason,
        }
    except Exception as exc:
        result = {"supported": False, "gpu_name": "", "compute_capability": "", "reason": str(exc)}

    _BF16_CACHE = dict(result)
    return dict(result)


def optimal_compute_settings(use_cache=True) -> dict:
    """Detect the optimal device and precision for the current machine, caching by default."""
    global _COMPUTE_CACHE
    if use_cache and _COMPUTE_CACHE is not None:
        return dict(_COMPUTE_CACHE)

    bf16_info = check_bf16_support(use_cache=use_cache)
    result = {"device": "cpu", "precision": "fp32"}
    try:
        import torch

        if torch.cuda.is_available():
            result = {"device": "cuda", "precision": "bf16" if bf16_info["supported"] else "fp32"}
        else:
            mps = getattr(torch, "mps", None)
            mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
            if (mps is not None and mps.is_available()) or (mps_backend is not None and mps_backend.is_available()):
                result = {"device": "mps", "precision": "fp32"}
    except Exception:
        pass

    _COMPUTE_CACHE = dict(result)
    return dict(result)

