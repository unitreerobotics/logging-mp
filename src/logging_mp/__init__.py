# logging_mp/__init__.py
import atexit
import datetime
import glob
import logging
import multiprocessing
import os
import platform
import queue as queue_module
import signal
import sys
import threading
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from rich.logging import RichHandler


# ----------------------------------------------------------------------
# Module-level variables
# ----------------------------------------------------------------------
_logging_mp_raw_log_queue = None
_logging_mp_log_queue = None

# ----------------------------------------------------------------------
# Internal Helpers
# ----------------------------------------------------------------------
class _logging_mp_SimpleQueueProxy:
    def __init__(self, queue):
        self._queue = queue

    def put_nowait(self, item):
        self._queue.put(item)

    def put(self, item):
        self._queue.put(item)

    def get(self, block=True, timeout=None):
        reader = getattr(self._queue, '_reader', None)
        if block:
            if timeout is None:
                return self._queue.get()
            if timeout < 0:
                raise ValueError('timeout must be non-negative')
            if reader is not None and not reader.poll(timeout):
                raise queue_module.Empty
            return self._queue.get()

        if reader is not None and not reader.poll(0):
            raise queue_module.Empty
        return self._queue.get()

    def qsize(self):
        return 0

class _logging_mp_QueueHandler(QueueHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except (BrokenPipeError, EOFError, OSError):
            # During shutdown, it is acceptable to lose the last few logs.
            return

def _logging_mp_get_prog_name():
    try:
        name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
        return name if name else "app"
    except Exception:
        return "app"

class TimestampedRotatingFileHandler(RotatingFileHandler):
    def __init__(self, log_dir, prog_name, maxBytes, backupCount, encoding=None):
        self._log_dir = log_dir
        self._prog_name = prog_name
        super().__init__(
            self._build_log_path(),
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
        )
        self._cleanup_old_logs()

    def _build_log_path(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return os.path.join(self._log_dir, f"{self._prog_name}_{timestamp}.log")

    def _log_pattern(self):
        return os.path.join(self._log_dir, f"{self._prog_name}_*.log")

    def _cleanup_old_logs(self):
        if self.backupCount <= 0:
            return
        log_files = sorted(glob.glob(self._log_pattern()))
        while len(log_files) > self.backupCount:
            oldest = log_files.pop(0)
            try:
                os.remove(oldest)
            except Exception:
                pass

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = os.path.abspath(self._build_log_path())
        if not self.delay:
            self.stream = self._open()
        self._cleanup_old_logs()

# ----------------------------------------------------------------------
# Listener and Wrapper Functions
# ----------------------------------------------------------------------
def _logging_mp_queue_listener(queue_proxy, config, prog_name):
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        pass
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    except Exception:
        pass
    handlers = []
    if config.get("console", True):
        handlers.append(
            RichHandler(
                show_time=True,
                log_time_format="%H:%M:%S.%f",
                omit_repeated_times=True,
                show_level=True,
                show_path=True,
                rich_tracebacks=True,
                markup=False
            )
        )
    if config.get("file", False):
        file_path = config.get("file_path", "logs")
        backup_count = config.get("backup_count", 10)
        max_file_size = config.get("max_file_size", 100 * 1024 * 1024)
        os.makedirs(file_path, exist_ok=True)
        file_handler = TimestampedRotatingFileHandler(
            log_dir=file_path,
            prog_name=prog_name,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(processName)s-%(threadName)s: %(message)s',
            datefmt='%H:%M:%S'
        ))
        handlers.append(file_handler)
    if not handlers:
        handlers.append(logging.NullHandler())

    listener = QueueListener(queue_proxy, *handlers, respect_handler_level=False)
    try:
        while True:
            record = queue_proxy.get()
            if record is None:
                break
            listener.handle(record)
    finally:
        for handler in handlers:
            try:
                handler.flush()
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass

def _logging_mp_prepare_child_logging(queue, level):
    global _logging_mp_raw_log_queue, _logging_mp_log_queue
    _logging_mp_raw_log_queue = queue
    _logging_mp_log_queue = queue
    _internal_manager._global_level = level
    _internal_manager._is_started = True
    _internal_manager._log_queue = queue

    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, QueueHandler)]
    root.setLevel(level)
    if queue:
        root.addHandler(_logging_mp_QueueHandler(queue))
    root.propagate = False

    for logger in list(logging.Logger.manager.loggerDict.values()):
        if not isinstance(logger, logging.Logger):
            continue
        logger.handlers = [h for h in logger.handlers if not isinstance(h, QueueHandler)]
        if queue:
            logger.addHandler(_logging_mp_QueueHandler(queue))
        logger.propagate = False

def _logging_mp_target_wrapper(queue, level, original_target, *args, **kwargs):
    _logging_mp_prepare_child_logging(queue, level)
    original_target(*args, **kwargs)

# ----------------------------------------------------------------------
# Main Class
# ----------------------------------------------------------------------
class LoggingMP:
    """Multiprocessing-safe logging manager with queue-based aggregation."""
    def __init__(self):
        self._log_queue = None
        self._listener_process = None
        self._global_level: int = logging.WARNING
        self._is_started: bool = False
        self._lock = threading.Lock()
        self._config = {
            "console": True,
            "file": False,
            "file_path": "logs",
            "backup_count": 10,
            "max_file_size": 100 * 1024 * 1024,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def basicConfig(
            self,
            level: int = logging.WARNING,
            console: bool = True,
            file: bool = False,
            file_path: str = "logs",
            backup_count: int = 10,
            max_file_size: int = 100 * 1024 * 1024
        ) -> None:
        """Configure the logging-mp system with global settings.
    
        Args:
            level (int): The global logging level (default: WARNING).
            console (bool): Enable console output (default: True).
            file (bool): Enable file output (default: False).
            file_path (str): Path for log files (default: "logs").
            backup_count (int): Number of backup log files to keep (default: 10).
            max_file_size (int): Maximum size in bytes for a single log file.
                Once exceeded, logging continues in a new timestamped log file.
                The total number of timestamped log files is limited by
                backup_count. (default: 100MB)
        
        Raises:
            RuntimeError: If logging is already started.
            ValueError: If neither console nor file is enabled.
        """
        if self._is_started:
            raise RuntimeError("Logging system has already been started. Please configure before any getLogger() occurs.")
        if backup_count < 1:
            raise ValueError("'backup_count' must be greater than or equal to 1.")
        if max_file_size < 1:
            raise ValueError("'max_file_size' must be greater than 0.")
        self._global_level = level
        self._config.update({
            "console": console,
            "file": file,
            "file_path": file_path,
            "backup_count": backup_count,
            "max_file_size": max_file_size,
        })
        if not console and not file:
            raise ValueError("At least one of 'console' or 'file' must be True.")
        
        logging.getLogger().setLevel(level)
        self._ensure_started()

    def getLogger(
            self, 
            name: str = None
        ) -> logging.Logger:
        """Get a logger instance configured for multiprocessing.
    
        Args:
            name (str, optional): The name of the logger (default: root logger).
        
        Returns:
            logging.Logger: A configured logger that sends logs to the central queue.
        
        Note:
            This clears existing handlers and adds a QueueHandler. Call after basicConfig.
        """
        self._ensure_started()
        logger = logging.getLogger(name)

        has_handler = any(isinstance(h, QueueHandler) for h in logger.handlers)
        if not has_handler and self._log_queue:
            handler = _logging_mp_QueueHandler(self._log_queue)
            logger.setLevel(self._global_level)
            logger.addHandler(handler)
            logger.propagate = False
        elif name is None:
            logger.setLevel(self._global_level)
        return logger

    # ------------------------------------------------------------------
    # Internal Logic
    # ------------------------------------------------------------------
    def _ensure_started(self):
        with self._lock:
            if self._is_started: return
            start_method = multiprocessing.get_start_method(allow_none=True)
            if start_method is None:
                start_method = 'spawn' if platform.system() == 'Windows' else 'fork'
            global _logging_mp_raw_log_queue, _logging_mp_log_queue
            if _logging_mp_raw_log_queue is None:
                if start_method == 'fork':
                    _logging_mp_raw_log_queue = multiprocessing.SimpleQueue()
                else:
                    _logging_mp_raw_log_queue = multiprocessing.Queue(-1)
    
            if start_method == 'fork':
                _logging_mp_log_queue = _logging_mp_SimpleQueueProxy(_logging_mp_raw_log_queue)
            else:
                _logging_mp_log_queue = _logging_mp_raw_log_queue

            self._log_queue = _logging_mp_log_queue

            if multiprocessing.current_process().name == "MainProcess":
                if self._listener_process is None:
                    prog_name = _logging_mp_get_prog_name()
                    
                    self._listener_process = multiprocessing.Process(
                        target=_logging_mp_queue_listener,
                        args=(_logging_mp_log_queue, self._config, prog_name),
                        name="LogListenerProcess",
                        daemon=False
                    )
                    self._listener_process.start()
                    atexit.register(self._shutdown)

            self._is_started = True

    def _shutdown(self):
        if multiprocessing.current_process().name != "MainProcess":
            return
        if self._log_queue:
            try:
                self._log_queue.put_nowait(None)
            except Exception:
                try:
                    self._log_queue.put(None)
                except Exception:
                    pass
        if self._listener_process:
            self._listener_process.join(timeout=5)
        self._is_started = False

# ----------------------------------------------------------------------
# Export User-Facing API
# ----------------------------------------------------------------------
NOTSET = logging.NOTSET
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL

_internal_manager = LoggingMP()
basicConfig = _internal_manager.basicConfig
getLogger = _internal_manager.getLogger

# ----------------------------------------------------------------------
# Patch for Spawn Compatibility
# ----------------------------------------------------------------------
def _apply_spawn_patch():
    start_method = multiprocessing.get_start_method(allow_none=True)
    if start_method is None:
        start_method = 'spawn' if platform.system() == 'Windows' else 'fork'
    if getattr(multiprocessing.Process, '_logging_mp_patched', False):
        return
    if start_method in ('spawn', 'forkserver'):
        original_init = multiprocessing.Process.__init__
        def _logging_mp_patch_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            global _logging_mp_raw_log_queue
            if _logging_mp_raw_log_queue is not None and self._target is not _logging_mp_queue_listener:
                self._logging_mp_queue = _logging_mp_raw_log_queue
                self._logging_mp_level = _internal_manager._global_level
                if self._target is not None:
                    original_target = self._target
                    self._target = _logging_mp_target_wrapper
                    self._args = (_logging_mp_raw_log_queue, _internal_manager._global_level, original_target) + self._args

        multiprocessing.Process.__init__ = _logging_mp_patch_init

        original_bootstrap = multiprocessing.Process._bootstrap
        def _logging_mp_patch_bootstrap(self, *args, **kwargs):
            try:
                if hasattr(self, '_logging_mp_queue') and self._logging_mp_queue is not None:
                    _logging_mp_prepare_child_logging(
                        self._logging_mp_queue,
                        getattr(self, '_logging_mp_level', _internal_manager._global_level),
                    )
            except Exception:
                pass
            return original_bootstrap(self, *args, **kwargs)

        multiprocessing.Process._bootstrap = _logging_mp_patch_bootstrap
        multiprocessing.Process._logging_mp_patched = True

_apply_spawn_patch()