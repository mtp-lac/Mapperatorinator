"""Run inference outside the UI process and stream its output through Qt."""

import codecs
import json
import os
import re
import signal
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QStandardPaths, QTimer, pyqtSignal

from desktop.core import ROOT


RESULT_PREFIX = "MAPPERATORINATOR_RESULT "
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class InferenceJob(QObject):
    output = pyqtSignal(str)
    progress = pyqtSignal(int)
    stage = pyqtSignal(str)
    finished = pyqtSignal(str, str)

    def __init__(self, parent=None):
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
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)

    @property
    def running(self):
        return not self._done

    def _command(self, request_path):
        # A pythonw launcher still needs a console Python for the worker's streams.
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        return str(executable), ["-u", "-m", "desktop.worker", str(request_path)]

    def start(self, model, overrides):
        if self.running:
            raise RuntimeError("Generation is already running.")
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
            self._done = False
            program, arguments = self._command(request_path)
            self.stage.emit("Loading model")
            process.start(program, arguments)
        except Exception:
            self._done = True
            self._cleanup()
            raise

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
        if self._log:
            self._log.write(line + "\n")
        if line.startswith(RESULT_PREFIX):
            try:
                result = json.loads(line[len(RESULT_PREFIX):])
                self.result_path = str(Path(result["path"]).resolve())
            except (ValueError, KeyError, TypeError):
                self.output.emit(line)
            return
        self.output.emit(line)
        progress = re.search(r"(?:^|\s)(\d{1,3})%\|", line)
        if progress:
            self.progress.emit(min(int(progress.group(1)), 100))
        stages = (
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
        if self.running:
            self._terminate(force=True)

    def _on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self._done:
            self.output.emit(f"Could not start Python: {self.process.errorString()}")
            self._finish("failed", self.process.errorString())

    def _on_finished(self, exit_code, exit_status):
        if self._done:
            return
        self._read_output()
        self._consume(self._decoder.decode(b"", final=True))
        if self._buffer:
            self._line(self._buffer)
            self._buffer = ""
        if self._cancelled:
            self._finish("cancelled", "Generation cancelled.")
        elif exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            self._finish("completed", "Generation complete.")
        else:
            self._finish("failed", f"Generation failed (exit code {exit_code}). See the log for details.")

    def _finish(self, status, message):
        if self._done:
            return
        self._done = True
        self._kill_timer.stop()
        self._cleanup()
        self.finished.emit(status, message)

    def _cleanup(self):
        if self._log:
            self._log.close()
            self._log = None
        if self._request:
            self._request.cleanup()
            self._request = None
        if self.process:
            self.process.deleteLater()
            self.process = None

    def shutdown(self):
        """Reap the worker before Qt tears down its process object."""
        if self.running:
            self._cancelled = True
            self._terminate(force=True)
            if self.process:
                self.process.waitForFinished(5000)
