"""Multiprocessing-safe logging.

Every process (fork / spawn / forkserver) sends its log records to one shared
queue; a single background listener in the main process drains that queue and
writes to a Rich console and/or rotating log files. Producers apply
backpressure instead of dropping, and a stalled console can never block the
listener (which would deadlock the producers), so logs stay reliable.

Usage:
    import logging_mp
    logging_mp.basicConfig(level=logging_mp.INFO, console=True, file=True)
    log = logging_mp.getLogger(__name__)
    log.info("hello")
"""
import atexit
import datetime
import glob
import logging
import multiprocessing
import os
import queue
import re
import sys
import threading
from logging.handlers import QueueHandler, RotatingFileHandler
from typing import Optional

from rich.logging import RichHandler

NOTSET, DEBUG, INFO, WARNING, ERROR, CRITICAL = (
    logging.NOTSET, logging.DEBUG, logging.INFO,
    logging.WARNING, logging.ERROR, logging.CRITICAL,
)

_FILE_FORMAT = logging.Formatter(
    fmt="%(asctime)s.%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] "
        "%(processName)s-%(threadName)s: %(message)s",
    datefmt="%H:%M:%S",
)

# The one cross-process queue. Producers put records here; the listener drains
# it. Children receive it by fork inheritance or by the spawn/forkserver
# injection patch at the bottom of this file.
_queue: Optional["multiprocessing.Queue"] = None


class _QueueHandler(QueueHandler):
    """Producer side: hand records to the shared queue with backpressure.

    stdlib QueueHandler uses put_nowait and drops when the queue's platform
    semaphore cap (~32k on macOS) is reached; we block instead so a burst
    throttles the producer rather than losing records. Overriding enqueue (not
    emit) is required: stdlib emit swallows a full queue inside handleError, so
    a subclass emit would never see it.
    """
    def enqueue(self, record):
        try:
            self.queue.put(record, timeout=3.0)
        except queue.Full:
            # Listener wedged or dead: surface to stderr rather than block the
            # producer forever or drop the record silently.
            sys.stderr.write("%s: %s\n" % (record.levelname, record.getMessage()))
        except (BrokenPipeError, EOFError, OSError, ValueError):
            pass  # queue already closed during interpreter shutdown


class _AsyncConsole(logging.Handler):
    """Run the blocking Rich console on its own thread so it can never stall
    the listener.

    The listener is the sole reader of the cross-process queue; if it blocks on
    a stuck console pipe (e.g. `prog | head`), every producer's feeder thread
    backs up and deadlocks at exit. Records go through a bounded in-memory queue
    drained here. The normal path is backpressure -- emit blocks briefly so a
    merely-slow-but-live console loses nothing -- and only a genuinely wedged
    console degrades to fast, counted, reported drops (recovering on its own
    once the console accepts records again).
    """
    def __init__(self, target):
        super().__init__()
        self._target = target
        self._queue = queue.Queue(10000)
        self._degraded = False
        self._dropped = 0
        self._thread = threading.Thread(target=self._drain, name="LogConsole", daemon=True)
        self._thread.start()

    def emit(self, record):
        try:
            if self._degraded:
                self._queue.put_nowait(record)
            else:
                self._queue.put(record, timeout=1.0)
        except queue.Full:
            self._degraded = True
            self._dropped += 1

    def _drain(self):
        while True:
            record = self._queue.get()
            if record is None:
                return
            try:
                self._target.handle(record)
                self._degraded = False  # console accepted a record: it's alive
            except Exception:
                pass

    def close(self):
        try:
            self._queue.put(None, timeout=1.0)
        except queue.Full:
            pass
        self._thread.join(timeout=5)  # daemon: reclaimed at exit if still wedged
        if self._dropped:
            sys.stderr.write(
                "logging_mp: dropped %d console record(s) because the console "
                "consumer stalled; file logging is unaffected\n" % self._dropped
            )
        try:
            self._target.close()
        except Exception:
            pass
        super().close()


class TimestampedRotatingFileHandler(RotatingFileHandler):
    """Rotate on size (maxBytes) and, when file_name_format uses a time
    directive, on each new time bucket. Old files beyond backupCount are pruned.
    """
    def __init__(self, log_dir, prog_name, maxBytes, backupCount, encoding=None, file_name_format=None):
        self._log_dir = log_dir
        self._prog_name = prog_name
        self._file_name_format = file_name_format
        self._current_file_name = None
        self._file_index = -1
        super().__init__(self._build_path(), maxBytes=maxBytes, backupCount=backupCount, encoding=encoding)
        self._prune()

    def _build_path(self):
        if not self._file_name_format:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            return os.path.join(self._log_dir, f"{self._prog_name}_{stamp}.log")
        name = self._current_name()
        if name != self._current_file_name:
            self._current_file_name = name
            stem, ext = os.path.splitext(name)
            existing = glob.glob(os.path.join(self._log_dir, f"{glob.escape(stem)}_*{glob.escape(ext)}"))
            indexes = [int(m.group(1)) for p in existing
                       for m in [re.search(r"_(\d+)$", os.path.splitext(p)[0])] if m]
            self._file_index = max(indexes, default=0)
        else:
            self._file_index += 1
        stem, ext = os.path.splitext(name)
        return os.path.join(self._log_dir, f"{stem}_{self._file_index}{ext}")

    def _current_name(self):
        name = datetime.datetime.now().strftime(self._file_name_format).format(prog_name=self._prog_name)
        return name if "{prog_name}" in self._file_name_format else f"{self._prog_name}_{name}"

    def _prune(self):
        if self.backupCount <= 0:
            return
        active = os.path.abspath(self.baseFilename)
        pattern = os.path.join(self._log_dir, f"{glob.escape(self._prog_name)}_*.log")
        others = [p for p in glob.glob(pattern) if os.path.abspath(p) != active]
        # Oldest first, by mtime then natural name; the active file counts toward
        # backupCount and is never removed.
        others.sort(key=lambda p: (os.stat(p).st_mtime_ns, [
            (1, int(s)) if s.isdigit() else (0, s) for s in re.split(r"(\d+)", os.path.basename(p))
        ]))
        while len(others) >= self.backupCount:
            try:
                os.remove(others.pop(0))
            except OSError:
                pass

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = os.path.abspath(self._build_path())
        if not self.delay:
            self.stream = self._open()
        self._prune()

    def emit(self, record):
        try:
            if self._file_name_format and self._current_name() != self._current_file_name:
                self.doRollover()
        except Exception:
            self.handleError(record)
        super().emit(record)  # stdlib FileHandler.emit guards its own I/O


class LoggingMP:
    """Owns the shared queue and the single listener thread."""
    def __init__(self):
        self._level = WARNING
        self._started = False
        self._listener = None
        self._lock = threading.Lock()
        self._config = dict(console=True, file=False, file_path="logs",
                            backup_count=10, max_file_size=100 * 1024 * 1024,
                            file_name_format=None)

    def basicConfig(self, level: int = WARNING, console: bool = True, file: bool = False,
                    file_path: str = "logs", backup_count: int = 10,
                    max_file_size: int = 100 * 1024 * 1024,
                    file_name_format: Optional[str] = None) -> None:
        """Configure logging-mp. Call once, before any getLogger().

        Args:
            level: global logging level (default WARNING).
            console: enable Rich console output.
            file: enable rotating file output.
            file_path: directory for log files.
            backup_count: number of log files to keep (>= 1).
            max_file_size: bytes per file before rotating to a new one.
            file_name_format: optional name with strftime directives and
                {prog_name}; custom names get a numeric suffix. None keeps a
                timestamped name.

        Raises:
            RuntimeError: if logging is already started.
            ValueError / TypeError: on invalid configuration.
        """
        if self._started:
            raise RuntimeError("Logging already started; configure before any getLogger().")
        if backup_count < 1:
            raise ValueError("'backup_count' must be >= 1.")
        if max_file_size < 1:
            raise ValueError("'max_file_size' must be > 0.")
        if file_name_format is not None and not isinstance(file_name_format, str):
            raise TypeError("'file_name_format' must be a string or None.")
        if file_name_format and os.path.basename(file_name_format) != file_name_format:
            raise ValueError("'file_name_format' must be a file name, not a path.")
        if file_name_format and not file_name_format.endswith(".log"):
            raise ValueError("'file_name_format' must end with '.log'.")
        if "{prog_name}" in (file_name_format or "") and not file_name_format.startswith("{prog_name}_"):
            raise ValueError("'file_name_format' must start with '{prog_name}_' when using {prog_name}.")
        try:
            datetime.datetime.now().strftime(file_name_format or "").format(prog_name="app")
        except (IndexError, KeyError, ValueError) as exc:
            raise ValueError(f"Invalid 'file_name_format': {file_name_format}") from exc
        if not console and not file:
            raise ValueError("At least one of 'console' or 'file' must be True.")

        self._level = level
        self._config.update(console=console, file=file, file_path=file_path,
                            backup_count=backup_count, max_file_size=max_file_size,
                            file_name_format=file_name_format)
        logging.getLogger().setLevel(level)
        self._ensure_started()

    def getLogger(self, name: Optional[str] = None) -> logging.Logger:
        """Return a logger whose records flow to the shared queue."""
        self._ensure_started()
        logger = logging.getLogger(name)
        if _queue is not None and not any(isinstance(h, QueueHandler) for h in logger.handlers):
            logger.setLevel(self._level)
            logger.addHandler(_QueueHandler(_queue))
            logger.propagate = False
        elif name is None:
            logger.setLevel(self._level)
        return logger

    def _ensure_started(self):
        with self._lock:
            if self._started:
                return
            global _queue
            if _queue is None:
                # A multiprocessing.Queue (not SimpleQueue): its feeder thread
                # decouples the producer from the OS pipe, so put(timeout=...)
                # honors the deadline instead of blocking on a full pipe.
                _queue = multiprocessing.Queue(-1)
            # The listener runs only in the main process, as a thread rather
            # than a child process: a spawned listener re-imports __main__ and
            # would die silently if that import crashes.
            if multiprocessing.current_process().name == "MainProcess" and self._listener is None:
                self._listener = threading.Thread(target=self._listen, args=(self._build_handlers(),),
                                                   name="LogListener", daemon=True)
                self._listener.start()
                atexit.register(self._shutdown)
            self._started = True

    def _build_handlers(self):
        handlers = []
        if self._config["console"]:
            handlers.append(_AsyncConsole(RichHandler(
                show_time=True, log_time_format="%H:%M:%S.%f", omit_repeated_times=True,
                show_level=True, show_path=True, rich_tracebacks=True, markup=False,
            )))
        if self._config["file"]:
            os.makedirs(self._config["file_path"], exist_ok=True)
            prog = os.path.splitext(os.path.basename(sys.argv[0]))[0] or "app"
            handler = TimestampedRotatingFileHandler(
                log_dir=self._config["file_path"], prog_name=prog,
                maxBytes=self._config["max_file_size"], backupCount=self._config["backup_count"],
                encoding="utf-8", file_name_format=self._config["file_name_format"],
            )
            handler.setFormatter(_FILE_FORMAT)
            handlers.append(handler)
        return handlers or [logging.NullHandler()]

    def _listen(self, handlers):
        # Drain one record at a time and fan it out. Every handler here returns
        # promptly (the console is async, the file is buffered), so the listener
        # is never away from the queue long enough to fill the semaphore.
        try:
            while True:
                record = _queue.get()
                if record is None:
                    break
                for handler in handlers:
                    handler.handle(record)
        finally:
            for handler in handlers:
                handler.close()

    def _shutdown(self):
        if multiprocessing.current_process().name != "MainProcess" or not self._listener:
            return
        try:
            _queue.put_nowait(None)
        except Exception:
            try:
                _queue.put(None, timeout=30)
            except Exception:
                pass
        # Bounded: a daemon listener is reclaimed at exit if a handler is wedged.
        self._listener.join(timeout=30)
        self._started = False


_manager = LoggingMP()
basicConfig = _manager.basicConfig
getLogger = _manager.getLogger


# ----------------------------------------------------------------------
# spawn / forkserver support: children don't inherit memory, so inject the
# shared queue into every Process and rewire its logging before its target runs.
# ----------------------------------------------------------------------
def _prepare_child(q, level):
    global _queue
    _queue = q
    _manager._level = level
    _manager._started = True
    loggers = [logging.getLogger()] + [l for l in logging.Logger.manager.loggerDict.values()
                                       if isinstance(l, logging.Logger)]
    for logger in loggers:
        logger.handlers = [h for h in logger.handlers if not isinstance(h, QueueHandler)]
        logger.addHandler(_QueueHandler(q))
        logger.propagate = False
    logging.getLogger().setLevel(level)


def _child_target(q, level, target, *args, **kwargs):
    _prepare_child(q, level)
    target(*args, **kwargs)


def _apply_spawn_patch():
    method = multiprocessing.get_start_method(allow_none=True) or multiprocessing.get_all_start_methods()[0]
    if method not in ("spawn", "forkserver") or getattr(multiprocessing.Process, "_logging_mp_patched", False):
        return
    original_init = multiprocessing.Process.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if _queue is not None:
            self._logging_mp_queue = _queue
            self._logging_mp_level = _manager._level
            if self._target is not None:
                self._args = (_queue, _manager._level, self._target) + tuple(self._args)
                self._target = _child_target

    original_bootstrap = multiprocessing.Process._bootstrap

    def patched_bootstrap(self, *args, **kwargs):
        q = getattr(self, "_logging_mp_queue", None)
        if q is not None:
            try:
                _prepare_child(q, getattr(self, "_logging_mp_level", _manager._level))
            except Exception:
                pass
        return original_bootstrap(self, *args, **kwargs)

    multiprocessing.Process.__init__ = patched_init
    multiprocessing.Process._bootstrap = patched_bootstrap
    multiprocessing.Process._logging_mp_patched = True


_apply_spawn_patch()
