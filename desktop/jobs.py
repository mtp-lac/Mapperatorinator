"""Run inference outside the UI process and stream its output through Qt."""

import codecs
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QStandardPaths, QTimer, pyqtSignal

from desktop.core import ROOT


MODEL_IDLE_TIMEOUT_MS = 5 * 60 * 1000
READY_PREFIX = "MAPPERATORINATOR_READY"
RESULT_PREFIX = "MAPPERATORINATOR_RESULT "
DONE_PREFIX = "MAPPERATORINATOR_DONE "
UNLOADED_PREFIX = "MAPPERATORINATOR_UNLOADED"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class InferenceJob(QObject):
    output = pyqtSignal(str)
    progress = pyqtSignal(int)
    stage = pyqtSignal(str)
    finished = pyqtSignal(str, str)
    model_unloaded = pyqtSignal()

    def __init__(self, parent=None, idle_timeout_ms=MODEL_IDLE_TIMEOUT_MS):
        super().__init__(parent)
        self.process = None
        self.log_path = None
        self.result_path = None
        self._request = None
        self._log = None
        self._buffer = ""
        self._decoder = None
        self._cancelled = False
        self._done = True
        self._resident = False
        self._unloading = False
        self._pending_command = None
        self._idle_timeout_ms = int(idle_timeout_ms)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self.unload_model)

    @property
    def running(self):
        return not self._done

    def _command(self, request_path):
        # A pythonw launcher still needs a console Python for the worker's streams.
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        return str(executable), ["-u", "-m", "desktop.worker", "--serve"]

    def _send_command(self, payload):
        if self.process is not None and self.process.state() == QProcess.ProcessState.Running:
            data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.process.write(data)

    def _flush_pending_command(self):
        if self._pending_command is not None:
            command = self._pending_command
            self._pending_command = None
            self._send_command(command)

    def start(self, model, overrides):
        if self.running:
            raise RuntimeError("Generation is already running.")
        self._idle_timer.stop()
        self.result_path = None
        self._cancelled = False
        self._buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"generation-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
        try:
            self._log = self.log_path.open("w", encoding="utf-8", buffering=1)
            self._request = tempfile.TemporaryDirectory(prefix="mapperatorinator-")
            request_path = Path(self._request.name) / "request.json"
            request_path.write_text(json.dumps({"model": model, "overrides": overrides}, ensure_ascii=False), encoding="utf-8")

            can_reuse = (
                self._resident
                and not self._unloading
                and self.process is not None
                and self.process.state() == QProcess.ProcessState.Running
            )
            if can_reuse:
                self._done = False
                self.stage.emit("Starting generation")
                self._send_command({"cmd": "run", "request_path": str(request_path)})
                return

            if self.process is not None:
                if self.process.state() != QProcess.ProcessState.NotRunning:
                    self._terminate(force=True)
                    self.process.waitForFinished(3000)
                self._cleanup_process()
            self._unloading = False

            process = QProcess(self)
            self.process = process
            process.setWorkingDirectory(str(ROOT))
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert("PYTHONUNBUFFERED", "1")
            environment.insert("PYTHONIOENCODING", "utf-8")
            environment.insert("PYTHONUTF8", "1")
            environment.insert("TOKENIZERS_PARALLELISM", "false")
            # The worker does not create a Qt application or inherit offscreen settings.
            process.setProcessEnvironment(environment)
            process.readyReadStandardOutput.connect(self._read_output)
            process.finished.connect(self._on_finished)
            process.errorOccurred.connect(self._on_error)
            program, arguments = self._command(request_path)
            self._resident = "--serve" in arguments
            self._done = False
            if self._resident:
                self._pending_command = {"cmd": "run", "request_path": str(request_path)}
                process.started.connect(self._flush_pending_command)
            else:
                self._pending_command = None
            self.stage.emit("Loading model")
            process.start(program, arguments)
        except Exception:
            self._done = True
            self._cleanup_run()
            if not self._resident or self.process is None or self.process.state() == QProcess.ProcessState.NotRunning:
                self._cleanup_process()
            raise

    def unload_model(self):
        """Ask an idle resident worker to unload its cached model and exit."""
        if self.running:
            return
        self._idle_timer.stop()
        process = self.process
        if process is None:
            return
        if process.state() == QProcess.ProcessState.NotRunning:
            self._resident = False
            self._unloading = False
            self._cleanup_process()
            return
        self._unloading = True
        self._send_command({"cmd": "quit"})
        process.closeWriteChannel()
        self._kill_timer.start(3000)

    def _read_output(self):
        if self.process is not None:
            raw = bytes(self.process.readAllStandardOutput())
            self._consume(self._decoder.decode(raw))

    def _consume(self, text):
        self._buffer += ANSI_ESCAPE.sub("", text)
        lines = re.split(r"[\r\n]", self._buffer)
        self._buffer = lines.pop()
        for line in lines:
            self._line(line)
        # Some libraries emit long diagnostic records without a newline.
        if len(self._buffer) > 32768:
            self._line(self._buffer)
            self._buffer = ""

    def _line(self, line):
        line = line.strip()
        if not line:
            return
        if line in {READY_PREFIX, UNLOADED_PREFIX}:
            return
        if self._log:
            self._log.write(line + "\n")
        if line.startswith(RESULT_PREFIX):
            try:
                result = json.loads(line[len(RESULT_PREFIX):])
                self.result_path = str(Path(result["path"]).resolve())
            except (ValueError, KeyError, TypeError):
                self.output.emit(line)
            return
        if line.startswith(DONE_PREFIX):
            try:
                done = json.loads(line[len(DONE_PREFIX):])
                status = done.get("status", "failed")
                default_msg = "Generation complete." if status == "completed" else "Generation failed."
                message = done.get("message", default_msg)
            except (ValueError, TypeError):
                status = "failed"
                message = "Generation failed."
            self._finish(status, message, keep_process=True)
            return
        self.output.emit(line)
        progress = re.search(r"(?:^|\s)(\d{1,3})%\|", line)
        if progress:
            self.progress.emit(min(int(progress.group(1)), 100))
        stages = (
            ("unloading previous model", "Unloading previous model"),
            ("reusing loaded", "Model ready (cached)"),
            ("super timing", "Generating accurate timing"),
            ("generating timing", "Generating timing"),
            ("generating positions", "Generating positions"),
            ("diffusion", "Generating positions"),
            ("generating beatmap", "Generating beatmap"),
            ("generating sequences", "Generating beatmap"),
            ("generated beatmap saved", "Saving beatmap"),
            ("generated .osz saved", "Saving beatmap archive"),
        )
        lowered = line.lower()
        for phrase, label in stages:
            if phrase in lowered:
                self.stage.emit(label)
                break

    def cancel(self):
        if not self.running or self._cancelled:
            return
        self._idle_timer.stop()
        self._cancelled = True
        self.stage.emit("Stopping generation…")
        self._terminate(force=False)
        self._kill_timer.start(3000)

    def _terminate(self, force=False):
        process = self.process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        if os.name != "nt":
            try:
                # The worker creates its own session before importing the model stack.
                os.killpg(int(process.processId()), signal.SIGKILL if force else signal.SIGTERM)
                return
            except (ProcessLookupError, PermissionError):
                pass
        if force:
            process.kill()
        else:
            process.terminate()

    def _kill(self):
        if self.running or self._unloading:
            self._terminate(force=True)

    def _on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self._done:
            self.output.emit(f"Could not start Python: {self.process.errorString()}")
            self._finish("failed", self.process.errorString())

    def _on_finished(self, exit_code, exit_status):
        self._read_output()
        if self._decoder is not None:
            self._consume(self._decoder.decode(b"", final=True))
        if self._buffer:
            self._line(self._buffer)
            self._buffer = ""
        if not self._done:
            if self._cancelled:
                self._finish("cancelled", "Generation cancelled.")
            elif exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
                self._finish("completed", "Generation complete.")
            else:
                self._finish("failed", f"Generation failed (exit code {exit_code}). See the log for details.")
            return
        self._idle_timer.stop()
        self._kill_timer.stop()
        was_resident = self._resident or self._unloading
        self._unloading = False
        self._resident = False
        self._cleanup_process()
        if was_resident:
            self.model_unloaded.emit()

    def _finish(self, status, message, keep_process=False):
        if self._done:
            return
        self._done = True
        self._kill_timer.stop()
        self._pending_command = None
        self._cleanup_run()
        if (
            keep_process
            and self._resident
            and self.process is not None
            and self.process.state() == QProcess.ProcessState.Running
        ):
            if self._idle_timeout_ms > 0:
                self._idle_timer.start(self._idle_timeout_ms)
        else:
            self._resident = False
            self._cleanup_process()
        self.finished.emit(status, message)

    def _cleanup_run(self):
        if self._log:
            self._log.close()
            self._log = None
        if self._request:
            self._request.cleanup()
            self._request = None

    def _cleanup_process(self):
        if self.process:
            self.process.deleteLater()
            self.process = None

    def shutdown(self):
        """Reap the worker before Qt tears down its process object."""
        self._idle_timer.stop()
        self._kill_timer.stop()
        process = self.process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            if self.running:
                self._cancelled = True
                self._terminate(force=True)
                process.waitForFinished(5000)
            else:
                self._unloading = True
                self._send_command({"cmd": "quit"})
                process.closeWriteChannel()
                if not process.waitForFinished(1500):
                    self._terminate(force=True)
                    process.waitForFinished(3500)
        self._unloading = False
        self._resident = False
        self._cleanup_run()
        self._cleanup_process()
