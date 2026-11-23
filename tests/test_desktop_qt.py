"""Qt integration tests; no model weights, network, or accelerator required."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QSettings, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from desktop.app import MainWindow
from desktop.core import export_settings, validate_settings
from desktop.credits import CreditsDialog
from desktop.jobs import InferenceJob


APP = QApplication.instance() or QApplication([])
APP.setApplicationName("Mapperatorinator Desktop Tests")
APP.setOrganizationName("Mapperatorinator Tests")


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, self.directory.name)
        self.window = MainWindow(restore=False)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.job.shutdown()
        self.window.hide()
        self.window.deleteLater()
        APP.processEvents()
        self.directory.cleanup()

    def test_default_form_and_web_settings_roundtrip(self):
        settings = self.window.settings()
        self.assertEqual(settings["model"], "v32")
        settings.update(title="音楽 — Title", fast_decoder_loop=True, precision="bf16")
        self.window.apply_settings(export_settings(settings))
        actual = self.window.settings()
        for key in ("title", "fast_decoder_loop", "precision"):
            self.assertEqual(actual[key], settings[key])
        self.assertEqual(self.window.pages.count(), 4)

    def test_model_and_mode_capabilities(self):
        self.window.fields["model"].setCurrentIndex(self.window.fields["model"].findData("v30"))
        self.assertFalse(self.window.descriptor_panel.isEnabled())
        self.assertFalse(self.window.fields["gamemode"].model().item(3).isEnabled())
        self.window.fields["model"].setCurrentIndex(self.window.fields["model"].findData("v32"))
        self.window.fields["gamemode"].setCurrentIndex(3)
        self.window.navigation.setCurrentRow(1)
        APP.processEvents()
        self.assertTrue(self.window.fields["keycount"].isVisible())
        self.assertFalse(self.window.fields["circle_size"].isVisible())
        self.assertTrue(self.window.fields["scroll_speed_ratio"].isVisible())

    def test_descriptor_click_and_keyboard_cycle(self):
        self.window.navigation.setCurrentRow(2)
        APP.processEvents()
        panel = self.window.descriptor_panel
        checkbox = panel.items["style/messy"]
        self.assertEqual(checkbox.checkState(), Qt.CheckState.Unchecked)
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=checkbox.rect().center())
        self.assertEqual(panel.selections(), (["style/messy"], []))
        QTest.keyClick(checkbox, Qt.Key.Key_Space)
        self.assertEqual(panel.selections(), ([], ["style/messy"]))
        self.assertIn("Discourage", checkbox.accessibleDescription())
        QTest.keyClick(checkbox, Qt.Key.Key_Space)
        self.assertEqual(panel.selections(), ([], []))

    def test_descriptor_model_mode_filter_and_remembered_states(self):
        panel = self.window.descriptor_panel
        self.assertEqual(len(panel.items), 79)
        panel.set_selections(["skillset/jumps"], ["expression/simple"])
        self.window.fields["gamemode"].setCurrentIndex(1)
        self.assertEqual(len(panel.items), 51)
        self.assertNotIn("skillset/jumps", panel.items)
        self.assertEqual(self.window.settings()["descriptors"], [])
        self.window.fields["gamemode"].setCurrentIndex(0)
        self.assertEqual(self.window.settings()["descriptors"], ["skillset/jumps"])
        self.window.fields["model"].setCurrentIndex(self.window.fields["model"].findData("v30"))
        self.assertEqual(panel.items, {})
        self.assertEqual(panel.selections(), ([], []))
        self.window.fields["model"].setCurrentIndex(self.window.fields["model"].findData("v32"))
        self.assertEqual(self.window.settings()["descriptors"], ["skillset/jumps"])

    def test_descriptor_import_export_and_unknown_filtering(self):
        self.window.apply_settings({
            "version": "1.0", "settings": {"model": "v32", "gamemode": 0},
            "descriptors": {"positive": ["style/messy", "not-supported", "skillset/jumps"], "negative": ["expression/simple", "style/messy"]},
        })
        values = self.window.settings()
        self.assertEqual(values["descriptors"], ["skillset/jumps"])
        self.assertEqual(values["negative_descriptors"], ["style/messy", "expression/simple"])
        exported = export_settings(values)
        self.assertNotIn("not-supported", exported["descriptors"]["positive"])
        self.window.apply_settings(exported)
        self.assertEqual(self.window.settings()["negative_descriptors"], values["negative_descriptors"])

    def test_descriptor_search_does_not_change_submission(self):
        panel = self.window.descriptor_panel
        panel.set_selections(["style/messy"], [])
        panel.search.setText("slider tech")
        self.assertTrue(panel.items["style/messy"].isHidden())
        self.assertEqual(self.window.settings()["descriptors"], ["style/messy"])
        self.assertEqual([key for key, card, _ in panel.groups if not card.isHidden()], ["tech"])
        panel.search.clear()
        self.assertFalse(panel.items["style/messy"].isHidden())

    def test_hidden_reference_actions_do_not_leak_into_generation(self):
        self.window.fields["beatmap_path"].edit.setText("reference.osu")
        self.window.fields["add_to_beatmap"].setChecked(True)
        self.window.fields["overwrite_reference_beatmap"].setChecked(True)
        self.window.contexts["TIMING"].setChecked(True)
        self.window.fields["beatmap_path"].edit.clear()
        values = self.window.settings()
        self.assertFalse(values["add_to_beatmap"])
        self.assertFalse(values["overwrite_reference_beatmap"])
        self.assertEqual(values["in_context"], [])

    def test_reference_fill_and_validation(self):
        directory = Path(self.directory.name)
        audio = directory / "song.mp3"
        audio.touch()
        reference = directory / "reference.osu"
        reference.write_text("osu file format v14\n[General]\nAudioFilename: song.mp3\nMode:3\n[Metadata]\nTitle:音楽\nArtist:Artist\n[Difficulty]\nCircleSize:7\n", encoding="utf-8")
        self.window.fields["beatmap_path"].edit.setText(str(reference))
        self.window.fill_reference()
        values = self.window.settings()
        self.assertEqual(values["title"], "音楽")
        self.assertEqual(values["gamemode"], 3)
        overrides = validate_settings(values)
        self.assertEqual(overrides["keycount"], 7)
        self.assertEqual(overrides["audio_path"], str(audio.resolve()))

    def test_close_during_generation_cancels_then_closes(self):
        self.window.job._done = False
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.close()
        self.assertTrue(self.window.isVisible())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(self.window.job, "cancel") as cancel:
            self.window.close()
        cancel.assert_called_once()
        self.assertTrue(self.window._closing)
        self.window.job._done = True
        self.window._job_finished("cancelled", "Generation cancelled.")
        APP.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_timing_only_export_import(self):
        self.window.fields["output_type"].setCurrentIndex(1)
        settings = export_settings(self.window.settings())
        self.window.fields["output_type"].setCurrentIndex(0)
        self.window.apply_settings(settings)
        self.assertEqual(self.window.settings()["output_type"], ["TIMING"])

    def test_invalid_generation_never_starts_worker(self):
        with patch.object(self.window.job, "start") as start:
            self.window.start_generation()
        start.assert_not_called()
        self.assertFalse(self.window.error_label.isHidden())
        self.assertFalse(self.window.job.running)

    def test_help_about_opens_credits_page(self):
        self.assertIn("Credits", self.window.about_action.text())
        with patch.object(CreditsDialog, "exec", return_value=CreditsDialog.DialogCode.Accepted) as exec_dialog:
            self.window.about_action.trigger()
        exec_dialog.assert_called_once()
        dialog = CreditsDialog(self.window)
        try:
            self.assertIn("Credits", dialog.windowTitle())
            text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
            for expected in ("OliBomby", "osuT5", "Hugging Face", "Jason Won", "Marvin", "osu!dreamer", "MIT License"):
                self.assertIn(expected, text)
        finally:
            dialog.deleteLater()


class JobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.location = patch("desktop.jobs.QStandardPaths.writableLocation", return_value=self.directory.name)
        self.location.start()
        self.job = InferenceJob()
        self.outputs = []
        self.progress = []
        self.results = []
        self.job.output.connect(self.outputs.append)
        self.job.progress.connect(self.progress.append)
        self.job.finished.connect(lambda *result: self.results.append(result))

    def tearDown(self):
        self.job.shutdown()
        self.location.stop()
        self.directory.cleanup()

    def run_job(self, script=None, program=None, cancel=False):
        loop = QEventLoop()
        self.job.finished.connect(lambda *_: loop.quit())
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(12000)
        command = (program, []) if program else (sys.executable, ["-u", "-c", script])
        with patch.object(self.job, "_command", return_value=command):
            self.job.start("v32", {"use_server": False})
        if cancel:
            QTimer.singleShot(150, self.job.cancel)
        loop.exec()
        timer.stop()
        self.assertTrue(self.results, "Worker did not finish before timeout")
        self.assertFalse(self.job.running)
        self.assertIsNone(self.job._request)
        self.assertIsNone(self.job.process)

    def test_success_unicode_cr_progress_and_result(self):
        script = (
            "import json,sys; "
            "sys.stdout.buffer.write('音楽\\n 42%|test\\r 100%|done\\n'.encode('utf-8')); sys.stdout.flush(); "
            "print('MAPPERATORINATOR_RESULT '+json.dumps({'path':'result.osu'}),flush=True)"
        )
        self.run_job(script)
        self.assertEqual(self.results[0][0], "completed")
        self.assertIn("音楽", self.outputs)
        self.assertEqual(self.progress, [42, 100])
        self.assertTrue(self.job.result_path.endswith("result.osu"))
        self.assertIn("音楽", self.job.log_path.read_text(encoding="utf-8"))

    def test_nonzero_exit_flushes_partial_stderr(self):
        self.run_job("import sys; sys.stderr.write('diagnostic without newline'); sys.exit(7)")
        self.assertEqual(self.results[0][0], "failed")
        self.assertIn("diagnostic without newline", self.outputs)
        self.assertIn("7", self.results[0][1])

    def test_failed_to_start(self):
        self.run_job(program=str(Path(self.directory.name) / "missing-python"))
        self.assertEqual(self.results[0][0], "failed")

    def test_cancel_reaps_worker(self):
        self.run_job("import time; print('ready',flush=True); time.sleep(60)", cancel=True)
        self.assertEqual(self.results[0][0], "cancelled")

    def test_request_paths_with_spaces_are_passed_as_arguments(self):
        script = "import json,pathlib,sys; data=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')); print(data['model'])"
        with patch.object(self.job, "_command", side_effect=lambda request: (sys.executable, ["-u", "-c", script, str(request)])):
            loop = QEventLoop()
            self.job.finished.connect(lambda *_: loop.quit())
            self.job.start("v32", {"title": "音楽"})
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
        self.assertEqual(self.results[0][0], "completed")
        self.assertIn("v32", self.outputs)


if __name__ == "__main__":
    unittest.main()
