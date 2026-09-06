"""Multiprocessing-safe logging: every process sends its records to one shared
queue drained by a single listener thread (Rich console and/or rotating files)."""
import atexit
import datetime
import glob
import logging
import multiprocessing
import multiprocessing.util  # noqa: F401  (import registers multiprocessing's exit hook)
import os
import queue
import re
import sys
import threading
import time
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

# The one cross-process queue; children receive it by fork inheritance or by
# the spawn/forkserver injection patch at the bottom of this file.
_queue: Optional["multiprocessing.Queue"] = None

# Listener heartbeat: stamped per record taken, sampled by producers.
_beat = None

# Set once the listener is torn down, so late records go to stderr.
_stopped = False


class _QueueHandler(QueueHandler):
    """Producer side: hand records to the shared queue with backpressure.

    Overriding enqueue (not emit) is required -- stdlib emit swallows queue.Full
    into handleError. A queue whose writer was SIGKILLed mid-put stops delivering
    with no error, so the listener heartbeat is sampled to fall back to stderr.
    """
    STALL_TIMEOUT = 5.0    # seconds without listener progress before falling back

    def __init__(self, queue):
        super().__init__(queue)
        self._checked = time.monotonic()
        self._seen = None      # no baseline yet; the first check only takes one
        self._wedged = False

    def enqueue(self, record):
        # Sampled before the put: an unchanged beat a window later proves the
        # previous record was never delivered (after the put, an idle system
        # would look dead). Not latched, so a mere stall recovers.
        now = time.monotonic()
        if _beat is not None and now - self._checked >= self.STALL_TIMEOUT:
            beat, was_wedged = _beat.value, self._wedged
            self._wedged = self._seen is not None and beat == self._seen
            self._checked, self._seen = now, beat
            if self._wedged != was_wedged:
                if self._wedged:
                    # Our feeder cannot drain a corrupted queue either, and a
                    # child would hang forever joining it at exit.
                    try:
                        self.queue.cancel_join_thread()
                    except AttributeError:
                        pass                       # a plain queue.Queue, in tests
                sys.stderr.write(
                    "logging_mp: log queue stopped delivering (a producer was probably "
                    "killed mid-write); falling back to stderr\n" if self._wedged else
                    "logging_mp: log queue is delivering again\n")
        if _stopped or self._wedged:
            sys.stderr.write("%s: %s\n" % (record.levelname, record.getMessage()))
            return
        try:
            self.queue.put(record, timeout=3.0)
        except queue.Full:
            # Listener wedged or dead: surface to stderr instead of dropping.
            sys.stderr.write("%s: %s\n" % (record.levelname, record.getMessage()))
        except (BrokenPipeError, EOFError, OSError, ValueError):
            pass  # queue already closed during interpreter shutdown


class _AsyncConsole(logging.Handler):
    """Run the blocking Rich console on its own thread so it can never stall the
    listener (which would deadlock every producer). Normal path is backpressure;
    a wedged console degrades to counted drops and recovers on its own.
    """
    def __init__(self, target):
        super().__init__()
        self._target = target
        self._queue = queue.Queue(10000)
        self._degraded = False
        self._dropped = 0
        self._queued = self._done = 0   # accepted vs. actually handled
        self._thread = threading.Thread(target=self._drain, name="LogConsole", daemon=True)
        self._thread.start()

    def emit(self, record):
        try:
            if self._degraded:
                self._queue.put_nowait(record)
            else:
                self._queue.put(record, timeout=1.0)
            self._queued += 1
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
            self._done += 1

    def close(self):
        try:
            self._queue.put(None, timeout=1.0)
        except queue.Full:
            pass
        self._thread.join(timeout=5)  # daemon: reclaimed at exit if still wedged
        if self._thread.is_alive():
            self._dropped += self._queued - self._done   # or the report misses the tail
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
    """Rotate on size and on each new time bucket; prune old files beyond
    backupCount."""
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
        # Oldest first; the active file counts toward backupCount and is kept.
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
                            file_name_format=None, queue_size=65536)

    def basicConfig(self, level: int = WARNING, console: bool = True, file: bool = False,
                    file_path: str = "logs", backup_count: int = 10,
                    max_file_size: int = 100 * 1024 * 1024,
                    file_name_format: Optional[str] = None,
                    queue_size: int = 65536) -> None:
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
            queue_size: records that may be in flight before producers are
                throttled; bounds memory. Clamped to the platform semaphore cap
                (32767 on macOS), and only honored on the first call.

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
        if queue_size < 1:
            raise ValueError("'queue_size' must be > 0.")
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
                            file_name_format=file_name_format, queue_size=queue_size)
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
            global _queue, _beat, _stopped
            if _queue is None:
                # Bounded: its feeder thread honors put(timeout=...), and the
                # bound keeps a fast producer from buffering without limit.
                from multiprocessing.synchronize import SEM_VALUE_MAX  # 32767 on macOS
                _queue = multiprocessing.Queue(min(self._config["queue_size"], SEM_VALUE_MAX))
                _beat = multiprocessing.Value("d", 0.0, lock=False)
            # A thread, not a spawned child: a spawned listener re-imports
            # __main__ and dies if that crashes.
            if multiprocessing.current_process().name == "MainProcess" and self._listener is None:
                self._listener = threading.Thread(target=self._listen, args=(self._build_handlers(), _beat),
                                                   name="LogListener", daemon=True)
                self._listener.start()
                _stopped = False           # a live listener means records flow again
            # After _shutdown() the listener object survives but is dead.
            self._started = not _stopped

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

    def _listen(self, handlers, beat):
        # One record at a time; every handler returns promptly (console async,
        # file buffered), so the queue never fills because of the listener.
        try:
            while True:
                record = _queue.get()
                beat.value = time.monotonic()    # producers watch this for liveness
                if record is None:
                    break
                for handler in handlers:
                    handler.handle(record)
        finally:
            for handler in handlers:
                handler.close()

    @staticmethod
    def _release_child_feeder():
        """Let a child exit even when a killed sibling corrupted the queue.

        multiprocessing joins this process's feeder at exit, and on a queue whose
        write lock a SIGKILLed sibling took, that join never returns. The lock
        alone is not proof (a healthy feeder blocked on a full pipe holds it
        too), so the listener must have consumed nothing while we waited.
        """
        lock = getattr(_queue, "_wlock", None)     # None on Windows, or a plain Queue
        if lock is None:
            return
        before = _beat.value if _beat is not None else None
        if lock.acquire(timeout=1.5):
            lock.release()                         # free: the feeder is not stuck
        elif before is None or _beat.value == before:
            _queue.cancel_join_thread()            # stuck: exit, dropping the tail

    def _shutdown(self):
        global _stopped
        if multiprocessing.current_process().name != "MainProcess" or not self._listener:
            return                        # children are handled by _child_exit_guard
        _stopped = True                   # late records go to stderr, not into the void
        try:
            _queue.put_nowait(None)
        except Exception:
            try:
                _queue.put(None, timeout=5)
            except Exception:
                pass
        # 5s, not 30: the listener drains ~80k records/s, so this is ample for a
        # real backlog while keeping exit quick when the queue is unusable.
        self._listener.join(timeout=5)
        if self._listener.is_alive():
            sys.stderr.write("logging_mp: listener did not finish draining; queued "
                             "records were lost (a producer killed mid-write leaves "
                             "the queue unusable)\n")
            # Otherwise multiprocessing's exit handler joins the dead feeder.
            _queue.cancel_join_thread()
        self._started = False


_manager = LoggingMP()
basicConfig = _manager.basicConfig
getLogger = _manager.getLogger

# Registered at import, not at basicConfig(): atexit runs LIFO, so registering
# later would tear the listener down before user handlers get to log.
atexit.register(_manager._shutdown)


def _child_exit_guard():
    if multiprocessing.current_process().name != "MainProcess":
        LoggingMP._release_child_feeder()


def _register_child_exit_guard(_obj=None):
    # Idempotent against the registry itself, not a flag: forkserver children
    # take both paths, and _after_fork's registry.clear() wipes the first one.
    for (priority, _), finalizer in multiprocessing.util._finalizer_registry.items():
        if priority == 16 and getattr(finalizer, "_callback", None) is _child_exit_guard:
            return
    multiprocessing.util.Finalize(None, _child_exit_guard, exitpriority=16)


# A multiprocessing finalizer (priority 16 runs before the feeder join at -5),
# not atexit, because children end via os._exit(). It cannot be registered at
# import either: _bootstrap clears the registry in children that fork. So
# fork/forkserver register here, and spawn -- whose _after_fork is a no-op --
# in _prepare_child.
multiprocessing.util.register_after_fork(_manager, _register_child_exit_guard)


# ----------------------------------------------------------------------
# spawn / forkserver support: children don't inherit memory, so inject the
# shared queue into every Process and rewire its logging before its target runs.
# ----------------------------------------------------------------------
def _prepare_child(q, beat=None, level=INFO):
    global _queue, _beat
    _queue = q
    _beat = beat
    _manager._level = level
    _manager._started = True
    loggers = [logging.getLogger()] + [l for l in logging.Logger.manager.loggerDict.values()
                                       if isinstance(l, logging.Logger)]
    for logger in loggers:
        logger.handlers = [h for h in logger.handlers if not isinstance(h, QueueHandler)]
        logger.addHandler(_QueueHandler(q))
        logger.propagate = False
    logging.getLogger().setLevel(level)
    # Spawn children never run after-fork hooks; register the guard here.
    _register_child_exit_guard()


def _child_target(q, beat, level, target, *args, **kwargs):
    _prepare_child(q, beat, level)
    target(*args, **kwargs)


def _apply_spawn_patch():
    """Wire the shared queue into children that do not inherit it.

    Installed unconditionally, with the start method tested per Process
    construction rather than here: a caller may set_start_method() after
    importing us, and 3.14 changed the POSIX default from fork to forkserver.
    An answer computed at import can therefore be wrong, and silently: the
    child would log into a queue nobody reads.
    """
    if getattr(multiprocessing.Process, "_logging_mp_patched", False):
        return
    original_init = multiprocessing.Process.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        method = (multiprocessing.get_start_method(allow_none=True)
                  or multiprocessing.get_all_start_methods()[0])
        if method not in ("spawn", "forkserver"):
            return                     # fork: the child inherits _queue as it is
        if _queue is not None:
            self._logging_mp_queue = _queue
            self._logging_mp_beat = _beat
            self._logging_mp_level = _manager._level
            if self._target is not None:
                self._args = (_queue, _beat, _manager._level, self._target) + tuple(self._args)
                self._target = _child_target

    original_bootstrap = multiprocessing.Process._bootstrap

    def patched_bootstrap(self, *args, **kwargs):
        q = getattr(self, "_logging_mp_queue", None)
        if q is not None:
            try:
                _prepare_child(q, getattr(self, "_logging_mp_beat", None),
                               getattr(self, "_logging_mp_level", _manager._level))
            except Exception:
                pass
        return original_bootstrap(self, *args, **kwargs)

    multiprocessing.Process.__init__ = patched_init
    multiprocessing.Process._bootstrap = patched_bootstrap
    multiprocessing.Process._logging_mp_patched = True


_apply_spawn_patch()
