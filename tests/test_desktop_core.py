"""CPU-only contracts for desktop settings and the one-shot worker."""

from contextlib import contextmanager, redirect_stdout
from enum import Enum
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from desktop import core, worker


class SettingsTests(unittest.TestCase):
    def validate(self, **changes):
        return core.validate_settings({"audio_path": "test.mp3", **changes}, check_paths=False)

    def test_defaults_and_fresh_lists(self):
        self.assertEqual(core.DEFAULTS["model"], "v32")
        self.assertEqual(core.DEFAULTS["difficulty"], "5")
        self.assertEqual(core.DEFAULTS["year"], "2023")
        self.assertEqual(core.DEFAULTS["precision"], "fp32")
        self.assertEqual(set(core.MODELS), {"v28", "v29", "v30", "v31", "v32-mini", "v32"})
        first = core.load_settings({"unknown": "ignored"})
        first["descriptors"].append("style/messy")
        self.assertEqual(core.load_settings({})["descriptors"], [])
        self.assertNotIn("unknown", first)

    def test_web_export_import_and_desktop_extras_round_trip(self):
        exported = {
            "version": "1.0",
            "settings": {"model": "v32-mini", "enable_bf16": True, "seed": "0",
                         "background_image": '日本語/artist\'s "image".jpg', "unrecognized": "x"},
            "descriptors": {"positive": ["style/messy"], "negative": ["expression/simple"]},
            "inContextOptions": ["TIMING"],
        }
        settings = core.load_settings(exported)
        self.assertEqual(settings["precision"], "bf16")
        self.assertEqual(settings["in_context"], ["TIMING"])
        settings["device"] = "cuda:1"
        settings["precision"] = "fp16"
        round_trip = core.export_settings(settings)
        self.assertEqual(round_trip["descriptors"], exported["descriptors"])
        self.assertEqual(round_trip["inContextOptions"], ["TIMING"])
        self.assertEqual(round_trip["settings"]["background_image"], exported["settings"]["background_image"])
        self.assertEqual(round_trip["settings"]["precision"], "fp16")
        self.assertFalse(round_trip["settings"]["enable_bf16"])
        self.assertEqual(core.load_settings(json.loads(json.dumps(round_trip))), settings)
        self.assertNotIn("unrecognized", settings)

    def test_fast_loop_options_round_trip_and_typed_validation(self):
        for key in ("fast_decoder_loop", "super_timing_fast_loop"):
            with self.subTest(key=key):
                self.assertIs(core.DEFAULTS[key], False)
                settings = core.load_settings({key: True})
                exported = core.export_settings(settings)
                self.assertIs(exported["settings"][key], True)
                self.assertIs(core.load_settings(exported)[key], True)
                self.assertIs(self.validate(**{key: "true"})[key], True)
                self.assertIs(self.validate(**{key: "false"})[key], False)
                with self.assertRaisesRegex(ValueError, key):
                    self.validate(**{key: "maybe"})

    def test_audio_extensions_match_inference(self):
        for suffix in ("mp3", "wav", "ogg", "m4a", "flac", "MP3", "WAV"):
            with self.subTest(suffix=suffix):
                self.validate(audio_path="song's 日本語." + suffix)
        for suffix in ("txt", "osu", "aac", ""):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "extension"):
                self.validate(audio_path="song." + suffix)

    def test_zero_values_and_blank_optional_fields(self):
        result = self.validate(seed="0", difficulty="0", hp_drain_rate="0", cfg_scale="0",
                               temperature="0", top_p="0", preview_time="0", start_time="0",
                               end_time="1", mapper_id="0", lora_path=" ", title=" ", year="")
        for key in ("seed", "difficulty", "hp_drain_rate", "cfg_scale", "temperature", "top_p",
                    "preview_time", "start_time", "mapper_id"):
            self.assertEqual(result[key], 0, key)
        for key in ("lora_path", "title", "year", "background", "descriptors", "negative_descriptors"):
            self.assertIsNone(result[key], key)
        self.assertFalse(result["use_server"])
        self.assertNotIn("model", result)
        self.assertIs(type(result["seed"]), int)
        self.assertIs(type(result["difficulty"]), float)

    def test_bad_ranges_nonfinite_and_nonintegral(self):
        bad = {
            "gamemode": [-1, 4, "1.5", None], "difficulty": [-0.1, "nan", "inf", "1e999"],
            "hp_drain_rate": [-1, 11], "circle_size": [-1, 11], "keycount": [0, 19],
            "overall_difficulty": [11], "approach_rate": [-1], "slider_multiplier": [3.7],
            "slider_tick_rate": [0, 4.1], "hold_note_ratio": [-0.01, 1.01],
            "scroll_speed_ratio": [-1, 2], "cfg_scale": [-1, "NaN"],
            "temperature": [-0.1, 1.1], "top_p": [-0.1, 1.1],
            "seed": [-1, "0.5", 2**32, True], "mapper_id": [-1, "1.1"],
            "preview_time": [-2, 600001], "start_time": [-1], "end_time": [-1],
        }
        for key, values in bad.items():
            for value in values:
                changes = {key: value}
                if key in {"keycount", "hold_note_ratio", "scroll_speed_ratio"}:
                    changes["gamemode"] = 3
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.validate(**changes)

    def test_time_range(self):
        for changes in ({"start_time": "20", "end_time": "10"},
                        {"start_time": "0", "end_time": "0"}, {"end_time": "0"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "end_time"):
                self.validate(**changes)
        self.assertEqual(self.validate(start_time="0", end_time="2")["start_time"], 0)

    def test_model_year_limits_and_v30_omissions(self):
        for model in core.MODELS:
            with self.subTest(model=model):
                if model == "v30":
                    result = self.validate(model=model, year="not a year", descriptors=["x"], hitsounded=False)
                    for key in ("year", "descriptors", "negative_descriptors", "hitsounded"):
                        self.assertNotIn(key, result)
                    with self.assertRaisesRegex(ValueError, "gamemode"):
                        self.validate(model=model, gamemode=3)
                    continue
                maximum = 2024 if model in {"v32", "v32-mini"} else 2023
                self.assertEqual(self.validate(model=model, year=str(maximum))["year"], maximum)
                for year in (2006, maximum + 1, "2023.5", "inf"):
                    with self.assertRaisesRegex(ValueError, "year"):
                        self.validate(model=model, year=year)

    def test_mode_specific_fields(self):
        for mode in range(4):
            result = self.validate(gamemode=mode, hold_note_ratio="0", scroll_speed_ratio="0")
            self.assertEqual("circle_size" in result, mode == 0)
            self.assertEqual("keycount" in result, mode == 3)
            self.assertEqual("hold_note_ratio" in result, mode == 3)
            self.assertEqual("scroll_speed_ratio" in result, mode in {1, 3})

    def test_guidance_descriptor_counts(self):
        with self.assertRaisesRegex(ValueError, "descriptor count"):
            self.validate(cfg_scale="2", descriptors=["style/messy", "style/clean"], negative_descriptors=["expression/simple"])
        with self.assertRaises(ValueError):
            self.validate(cfg_scale="2", descriptors=[], negative_descriptors=["expression/simple"])
        self.validate(cfg_scale="2", descriptors=["style/messy", "style/clean"], negative_descriptors=[])
        self.validate(cfg_scale="2", descriptors=["style/messy"], negative_descriptors=["expression/simple"])
        self.validate(cfg_scale="1", descriptors=["style/messy", "style/clean"], negative_descriptors=["expression/simple"])

    def test_unsupported_descriptors_cannot_reach_inference(self):
        for key in ("descriptors", "negative_descriptors"):
            for changes in ({key: ["not-a-descriptor"]}, {"model": "v31", key: ["style/messy"]},
                            {"gamemode": 1, key: ["skillset/jumps"]}):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "unavailable"):
                    self.validate(**changes)
        self.assertEqual(self.validate(descriptors=["style/messy", "style/messy"])["descriptors"], ["style/messy"])

    def test_reference_requirements_and_capabilities(self):
        for changes in ({"in_context": ["TIMING"]}, {"add_to_beatmap": True},
                        {"overwrite_reference_beatmap": True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "reference"):
                self.validate(**changes)
        with self.assertRaisesRegex(ValueError, "requires add_to_beatmap"):
            self.validate(beatmap_path="ref.osu", overwrite_reference_beatmap=True)
        for model in core.MODELS:
            for name in ("TIMING", "MAP", "GD", "KIAI", "NO_HS", "SV", "invalid"):
                accepted = name == "TIMING" or (model in {"v28", "v29", "v31"} and name in core._CONTEXTS)
                with self.subTest(model=model, context=name):
                    if accepted:
                        self.assertEqual(self.validate(model=model, beatmap_path="ref.osu", in_context=[name])["in_context"], [name])
                    else:
                        with self.assertRaisesRegex(ValueError, "in_context"):
                            self.validate(model=model, beatmap_path="ref.osu", in_context=[name])
        self.assertEqual(self.validate(in_context=["NONE"])["in_context"], [])
        self.assertEqual(self.validate(output_type=["TIMING", "MAP", "SV"])["output_type"], ["TIMING", "MAP", "SV"])

    def test_invalid_types_and_choices(self):
        for changes in ({"model": "v99"}, {"device": "cuda:foo"}, {"precision": "int8"},
                        {"hitsounded": "maybe"}, {"descriptors": "tag"},
                        {"in_context": "TIMING"}, {"output_type": []}, {"output_type": ["invalid"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(**changes)
        self.assertFalse(self.validate(export_osz="false")["export_osz"])
        self.assertTrue(self.validate(hitsounded="on")["hitsounded"])


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="desktop 日本語 '")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.audio = self.root / "artist's 音楽.mp3"
        self.audio.write_bytes(b"audio placeholder")
        self.reference = self.root / "reference's 譜面.osu"
        self.reference.write_text(
            '\ufeffosu file format v14\n[General]\nAudioFilename: "' + self.audio.name + '"\n'
            'Mode: 0\nPreviewTime: 0\n[Metadata]\nTitle: Song: subtitle\nTitleUnicode: 音楽\n'
            'Artist: Example\nCreator: Mapper\nVersion: Hard\nBeatmapID: 123\n'
            '[Difficulty]\nHPDrainRate: 5\nCircleSize: 4\nOverallDifficulty: 8\n'
            'ApproachRate: 9\nSliderMultiplier: 1.4\nSliderTickRate: 1\n', encoding="utf-8")

    def test_metadata_and_auto_paths(self):
        info = core.read_reference(self.reference)
        self.assertEqual(info["audio_path"], str(self.audio))
        self.assertEqual(info["title"], "Song: subtitle")
        self.assertEqual(info["title_unicode"], "音楽")
        self.assertEqual(info["preview_time"], 0)
        self.assertEqual(info["beatmap_id"], 123)
        result = core.validate_settings({"beatmap_path": str(self.reference)})
        self.assertEqual(result["audio_path"], str(self.audio))
        self.assertEqual(result["output_path"], str(self.root))
        self.assertIsNone(result["lora_path"])

    def test_relative_paths_resolve_against_root_not_cwd(self):
        with patch.object(core, "ROOT", self.root):
            result = core.validate_settings({"audio_path": self.audio.name,
                                             "output_path": 'new 日本語/artist\'s "folder"'}, check_paths=False)
        self.assertEqual(result["audio_path"], str(self.audio))
        self.assertEqual(Path(result["output_path"]), self.root / 'new 日本語/artist\'s "folder"')
        result = core.validate_settings({"audio_path": str(self.audio)})
        self.assertEqual(result["output_path"], str(self.root))

    def test_background_and_lora_file_or_folder(self):
        background = self.root / "image's 日本語.png"
        background.write_bytes(b"image placeholder")
        lora = self.root / "LoRA"
        lora.mkdir()
        weights = lora / "adapter.safetensors"
        weights.write_bytes(b"weights placeholder")
        for path in (lora, weights):
            result = core.validate_settings({"audio_path": str(self.audio), "background_image": str(background), "lora_path": str(path)})
            self.assertEqual(result["background"], str(background))
            self.assertEqual(result["lora_path"], str(path))
        for changes in ({"audio_path": str(self.root)}, {"background_image": str(self.root)},
                        {"lora_path": str(self.root / "missing")}, {"output_path": str(self.audio)},
                        {"output_path": str(self.audio / "subdir")}, {"beatmap_path": str(self.audio)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                core.validate_settings({"audio_path": str(self.audio), **changes})

    def test_missing_audio_and_unsafe_reference_audio(self):
        self.audio.unlink()
        with self.assertRaisesRegex(ValueError, "audio_path"):
            core.validate_settings({"beatmap_path": str(self.reference)})
        for filename in ("../outside.mp3", "C:\\outside.mp3", "/outside.mp3", "\\\\server\\share\\audio.mp3"):
            self.reference.write_text("[General]\nAudioFilename: " + filename, encoding="utf-8")
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, "AudioFilename"):
                core.read_reference(self.reference)
        self.reference.write_text("[General]\nMode: 0", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "AudioFilename"):
            core.validate_settings({"beatmap_path": str(self.reference)})

    def test_reference_mode_when_adding(self):
        with self.assertRaisesRegex(ValueError, "gamemode"):
            core.validate_settings({"beatmap_path": str(self.reference), "gamemode": 3, "add_to_beatmap": True})
        core.validate_settings({"beatmap_path": str(self.reference), "gamemode": 3, "in_context": ["TIMING"]})

    def test_web_catalog_rulesets_groups_and_values(self):
        for model in ("v28", "v29", "v31"):
            for mode in range(4):
                self.assertEqual(len(core.descriptor_catalog(model, mode)), 72)
        for model in ("v32", "v32-mini"):
            for mode, count in enumerate((79, 51, 47, 59)):
                catalog = core.descriptor_catalog(model, mode)
                self.assertEqual(len(catalog), count)
                self.assertTrue(all(item["ruleset_id"] is None or str(item["ruleset_id"]) == str(mode) for item in catalog))
        catalog = core.descriptor_catalog("v32", 0)
        self.assertEqual(list(dict.fromkeys(item["group"] for item in catalog)), [
            "style", "expression", "gimmick", "skillset", "tech", "sliders",
            "jumps", "streams", "reading", "meta", "context", "additions",
        ])
        self.assertEqual(catalog[0]["label"], "messy")
        self.assertEqual(catalog[0]["group_title"], "Style")
        self.assertEqual(catalog[0]["value"], "style/messy")
        self.assertEqual(core.descriptor_catalog("v28", 0)[0]["value"], "gimmick")
        self.assertEqual(core.descriptor_catalog("v30", 0), [])
        self.assertEqual(core.descriptor_catalog("v32", "1"), core.descriptor_catalog("v32", 1))

    def test_catalogs_and_missing_datasets(self):
        for model in ("v28", "v32", "v32-mini"):
            entries = core.descriptor_catalog(model)
            self.assertTrue(entries)
            self.assertEqual(set(entries[0]), {"value", "label", "description", "ruleset_id", "group", "group_title"})
        self.assertEqual(core.descriptor_catalog("v28")[0]["value"], "gimmick")
        self.assertEqual(core.descriptor_catalog("v32")[0]["value"], "style/messy")
        self.assertEqual(core.descriptor_catalog("v30"), [])
        with patch.object(core, "ROOT", self.root):
            self.assertEqual(core.descriptor_catalog("v32"), [])


class WorkerTests(unittest.TestCase):
    def test_imports_are_lightweight_in_fresh_interpreter(self):
        source = ("import sys; import desktop.core, desktop.worker; "
                  "assert not any(x in sys.modules for x in ['torch', 'hydra', 'PyQt6', 'config', 'inference'])")
        process = subprocess.run([sys.executable, "-c", source], cwd=core.ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_worker_request_error_exit(self):
        process = subprocess.run([sys.executable, "-u", "-m", "desktop.worker", str(core.ROOT / "missing-request.json")],
                                 cwd=core.ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, 1)
        self.assertIn("Traceback", process.stderr)
        self.assertNotIn("MAPPERATORINATOR_RESULT", process.stdout)

    def test_worker_composes_and_converts_enums_and_emits_result(self):
        class ContextType(Enum):
            TIMING = "timing"
            MAP = "map"
            SV = "sv"

        cfg = types.SimpleNamespace(use_server=True, in_context=[], output_type=[], title=None)
        observed = {}

        @contextmanager
        def initialize(**kwargs):
            observed["initialize"] = kwargs
            yield

        def compose(**kwargs):
            observed["compose"] = kwargs
            return cfg

        def infer(config):
            observed["config"] = config
            return "content", Path("output's 日本語.osu")

        wrapped = types.SimpleNamespace(__wrapped__=infer)
        stubs = {
            "config": types.ModuleType("config"),
            "hydra": types.SimpleNamespace(compose=compose, initialize_config_dir=initialize),
            "omegaconf": types.SimpleNamespace(OmegaConf=types.SimpleNamespace(to_object=lambda value: value)),
            "osuT5.osuT5.event": types.SimpleNamespace(ContextType=ContextType),
            "inference": types.SimpleNamespace(main=wrapped),
        }
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory).resolve() / "request's 日本語.json"
            request.write_text(json.dumps({"model": "v32", "overrides": {
                "in_context": ["TIMING"], "output_type": ["MAP", "SV"], "title": "音楽", "use_server": True,
            }}), encoding="utf-8")
            stdout = io.StringIO()
            with (patch.dict(sys.modules, stubs),
                  patch.object(worker.os, "setsid", create=True),
                  patch.object(worker.os, "getsid", return_value=10, create=True),
                  patch.object(worker.os, "getpid", return_value=20), redirect_stdout(stdout)):
                code = worker.main([str(request)])
        self.assertEqual(code, 0)
        self.assertFalse(cfg.use_server)
        self.assertEqual(cfg.in_context, [ContextType.TIMING])
        self.assertEqual(cfg.output_type, [ContextType.MAP, ContextType.SV])
        self.assertEqual(cfg.title, "音楽")
        self.assertEqual(observed["compose"], {"config_name": "v32"})
        self.assertEqual(observed["initialize"]["version_base"], "1.1")
        self.assertEqual(Path(observed["initialize"]["config_dir"]), core.ROOT / "configs" / "inference")
        marker = stdout.getvalue().split("\nMAPPERATORINATOR_RESULT ", 1)[1]
        self.assertEqual(json.loads(marker), {"path": "output's 日本語.osu"})

    def _model_cache_stubs(self):
        class ContextType(Enum):
            TIMING = "timing"
            MAP = "map"
            SV = "sv"

        state = {
            "load_calls": [],
            "generate_calls": [],
            "cleared_models": [],
            "cuda_empty_calls": 0,
        }

        def make_cfg(model_name):
            return types.SimpleNamespace(
                model_path=f"OliBomby/Mapperatorinator-{model_name}",
                lora_path=None,
                auto_select_gamemode_model=True,
                gamemode=0,
                device="cpu",
                precision="fp32",
                attn_implementation="sdpa",
                seed=42,
                max_batch_size=16,
                fast_decoder_loop=False,
                generate_positions=False,
                diff_ckpt="",
                diff_refine_ckpt="",
                compile=False,
                train=types.SimpleNamespace(),
                diffusion=types.SimpleNamespace(),
                beatmap_path="",
                audio_path="song.mp3",
                difficulty=5.0,
                temperature=0.9,
                descriptors=None,
                in_context=[],
                output_type=[ContextType.MAP],
                use_server=False,
            )

        @contextmanager
        def initialize(**kwargs):
            yield

        def load_model_with_server(model_path, *args, **kwargs):
            model_obj = {"model_path": model_path, "lora": kwargs.get("lora_path"), "gamemode": kwargs.get("gamemode")}
            tok_obj = {"tokenizer_for": model_path}
            state["load_calls"].append(model_obj)
            return model_obj, tok_obj

        def generate(cfg, **kwargs):
            state["generate_calls"].append({
                "model": kwargs["model"],
                "difficulty": cfg.difficulty,
                "temperature": cfg.temperature,
                "descriptors": cfg.descriptors,
                "seed": cfg.seed,
            })
            return "osu_data", Path(f"out-{cfg.difficulty}.osu")

        def clear_decoder_cache(model):
            state["cleared_models"].append(model)

        stubs = {
            "config": types.ModuleType("config"),
            "hydra": types.SimpleNamespace(
                compose=lambda config_name: make_cfg(config_name),
                initialize_config_dir=initialize,
            ),
            "omegaconf": types.SimpleNamespace(OmegaConf=types.SimpleNamespace(to_object=lambda v: v)),
            "osuT5": types.ModuleType("osuT5"),
            "osuT5.osuT5": types.ModuleType("osuT5.osuT5"),
            "osuT5.osuT5.event": types.SimpleNamespace(ContextType=ContextType),
            "osuT5.osuT5.utils": types.SimpleNamespace(
                resolve_model_checkpoint_path=lambda ckpt, gamemode=None, auto_select_gamemode_model=True: (ckpt, f"gamemode={gamemode}"),
                get_model_checkpoint_subfolder=lambda ckpt, subfolder=None: subfolder or "",
                resolve_compatible_lora_path=lambda lora, ckpt_subfolder=None, verbose=True: (lora, None),
            ),
            "osuT5.osuT5.inference": types.ModuleType("osuT5.osuT5.inference"),
            "osuT5.osuT5.inference.compiled_decode": types.SimpleNamespace(clear_decoder_cache=clear_decoder_cache),
            "inference": types.SimpleNamespace(
                compile_args=lambda cfg: None,
                setup_inference_environment=lambda seed: None,
                load_model_with_server=load_model_with_server,
                should_load_separate_timing_model=lambda cfg: False,
                get_config=lambda cfg: ({"diff": cfg.difficulty}, {"title": "Test"}),
                generate=generate,
            ),
            "torch": types.SimpleNamespace(
                cuda=types.SimpleNamespace(
                    is_available=lambda: True,
                    empty_cache=lambda: state.__setitem__("cuda_empty_calls", state["cuda_empty_calls"] + 1),
                )
            ),
        }
        return stubs, state

    def test_model_cache_reuses_for_new_values_and_unloads_on_switch(self):
        stubs, state = self._model_cache_stubs()
        cache = worker.ModelCache()
        stdout = io.StringIO()
        with patch.dict(sys.modules, stubs), redirect_stdout(stdout), patch.object(worker.gc, "collect") as gc_collect:
            res1 = cache.run_request({"model": "v32", "overrides": {"difficulty": 5.0, "temperature": 0.9, "seed": 1}})
            res2 = cache.run_request({"model": "v32", "overrides": {"difficulty": 6.5, "temperature": 0.7, "seed": 99, "descriptors": ["style/clean"]}})
            self.assertEqual(len(state["load_calls"]), 1)
            self.assertEqual(len(state["generate_calls"]), 2)
            self.assertEqual(state["generate_calls"][0]["difficulty"], 5.0)
            self.assertEqual(state["generate_calls"][1]["difficulty"], 6.5)
            self.assertEqual(state["generate_calls"][1]["temperature"], 0.7)
            self.assertEqual(state["generate_calls"][1]["descriptors"], ["style/clean"])
            self.assertIs(state["generate_calls"][0]["model"], state["generate_calls"][1]["model"])
            self.assertEqual(res1[1], Path("out-5.0.osu"))
            self.assertEqual(res2[1], Path("out-6.5.osu"))

            # Switching to another model unloads the first model immediately before loading the second.
            first_model = cache.model
            cache.run_request({"model": "v31", "overrides": {"difficulty": 4.2}})
            self.assertEqual(len(state["load_calls"]), 2)
            self.assertEqual(state["cleared_models"], [first_model])
            self.assertGreaterEqual(gc_collect.call_count, 1)
            self.assertGreaterEqual(state["cuda_empty_calls"], 1)
            self.assertEqual(cache.model["model_path"], "OliBomby/Mapperatorinator-v31")

            # Explicit unload clears references and emits UNLOADED_MARKER.
            second_model = cache.model
            self.assertTrue(cache.unload())
            self.assertIsNone(cache.model)
            self.assertIsNone(cache.tokenizer)
            self.assertIsNone(cache.main_key)
            self.assertEqual(state["cleared_models"], [first_model, second_model])
            self.assertFalse(cache.unload())
        text = stdout.getvalue()
        self.assertIn("Reusing loaded Mapperatorinator V32", text)
        self.assertIn("Switching model — unloading previous model...", text)
        self.assertIn(worker.UNLOADED_MARKER, text)

    def test_serve_loop_handles_run_unload_and_quit(self):
        stubs, state = self._model_cache_stubs()
        with tempfile.TemporaryDirectory() as directory:
            req1 = Path(directory).resolve() / "req1.json"
            req2 = Path(directory).resolve() / "req2.json"
            req1.write_text(json.dumps({"model": "v32", "overrides": {"difficulty": 4.0}}), encoding="utf-8")
            req2.write_text(json.dumps({"model": "v32", "overrides": {"difficulty": 6.0}}), encoding="utf-8")
            stdin = io.StringIO(
                json.dumps({"cmd": "run", "request_path": str(req1)}) + "\n"
                + json.dumps({"cmd": "run", "request_path": str(req2)}) + "\n"
                + json.dumps({"cmd": "unload"}) + "\n"
                + json.dumps({"cmd": "quit"}) + "\n"
            )
            stdout = io.StringIO()
            with patch.dict(sys.modules, stubs), redirect_stdout(stdout):
                code = worker.serve(stdin=stdin)
        self.assertEqual(code, 0)
        self.assertEqual(len(state["load_calls"]), 1)
        self.assertEqual(len(state["generate_calls"]), 2)
        out = stdout.getvalue()
        self.assertIn(worker.READY_MARKER, out)
        self.assertIn(worker.UNLOADED_MARKER, out)
        self.assertEqual(out.count(worker.DONE_PREFIX), 2)


if __name__ == "__main__":
    unittest.main()
