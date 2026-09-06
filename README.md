<div align="center">
  
<div style="display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 20px;">
  <img src="https://raw.githubusercontent.com/unitreerobotics/logging-mp/main/logging_mp.svg" style="width: 45%; min-width: 250px; max-width: 300px;">
  <span style="color: #ddd; font-size: 30px;">        </span>
  <a href="https://www.unitree.com/">
    <img src="https://www.unitree.com/images/0079f8938336436e955ea3a98c4e1e59.svg" style="width: 45%; min-width: 250px; max-width: 300px;">
  </a>
</div>

<br>
<a>English</a> | <a href="README_zh-CN.md">中文</a>
</div>

**logging_mp** is a Python library specifically designed for **multiprocessing support** in logging. 

It solves the common logging problems in multiprocessing environments, especially interleaved output and file writing conflicts. In `spawn` mode, `logging_mp` uses **Monkey Patch** technology to connect child processes to a central logging queue automatically.

## 1. ✨ Features

* ⚡ **Support Multi-Processing & Thread:** Fully compatible with `threading` modules. Child processes automatically send logs to the main process.
* 💻 **Cross-Platform Support:** Works seamlessly with both `fork` (Linux) and `spawn` (Windows/macOS) start methods.
* 🎨 **Rich Integration:** Beautiful, colorized console output powered by [Rich](https://github.com/Textualize/rich).
* 📂 **File Logging:** Aggregates logs from all processes and threads into timestamped log files with size-based rollover and count-based cleanup.
* 🔒**Thread Safe:** Fully compatible with `threading` modules.

## 2. 🛠️ Installation

### 2.1 from source

```bash
git clone https://github.com/silencht/logging-mp
cd logging_mp
pip install -e .
```

### 2.2 from PyPI

```bash
pip install logging-mp
```

## 3. 🚀 Quick Start

Using `logging_mp` feels very close to using the standard logging module. You only need one initialization step in the main process entry point.

### 3.1 Basic Example

Initialize the logging system in your entry point script (for example, `main.py`) **before** creating any processes.

```python
import multiprocessing
import time

import logging_mp
# Call basicConfig before creating any processes or importing submodules that create loggers.
# In spawn mode, this automatically wires child processes to the log queue (monkey patch)
# and starts the background listener.
logging_mp.basicConfig(
    level=logging_mp.INFO, 
    console=True, 
    file=True,
    file_path="logs",
    backup_count=10,
    max_file_size=100 * 1024 * 1024
)
# Get a logger
logger_mp = logging_mp.getLogger(__name__)

def worker_task(name):
    # In the child process, just get a logger and write logs.
    # No manual queue or listener setup is needed.
    worker_logger_mp = logging_mp.getLogger("worker")
    worker_logger_mp.info(f"👋 Hello from {name} (PID: {multiprocessing.current_process().pid})")
    time.sleep(0.5)

if __name__ == "__main__":
    logger_mp.info("🚀 Starting processes...")
    
    processes = []
    for i in range(3):
        p = multiprocessing.Process(target=worker_task, args=(f"Worker-{i}",))
        p.start()
        processes.append(p)
        
    for p in processes:
        p.join()
    
    logger_mp.info("✅ All tasks finished.")
```

### 3.2 Configuration Options

The `basicConfig` method accepts the following arguments:

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `level` | `int` | `logging_mp.WARNING` | The global logging threshold (e.g., `INFO`, `DEBUG`). |
| `console` | `bool` | `True` | Enable/Disable Rich console output. |
| `file` | `bool` | `False` | Enable/Disable writing to a log file. |
| `file_path` | `str` | `"logs"` | Directory to store log files. |
| `backup_count` | `int` | `10` | Maximum total number of log files retained for this program. The oldest files are deleted first. |
| `max_file_size` | `int` | `100*1024*1024` | Maximum size of one log file in bytes. A new file is created when the current file reaches this size. |
| `file_name_format` | `str` | `None` | Optional name format using `strftime` directives and `{prog_name}`. See below for details. |
| `queue_size` | `int` | `65536` | Records that may be in flight before producers are throttled, bounding memory. Clamped to the platform semaphore cap (32767 on macOS); only the first call takes effect. |

#### File Naming and Rotation

Without `file_name_format`, every start and size rollover creates a timestamped file:

```text
example_20260324_153000_123456.log
```

With `file_name_format="{prog_name}_%Y%m%d.log"`, logs use a date and sequential index:

```text
example_20260324_0.log
example_20260324_1.log
example_20260325_0.log
```

- Starting the program again on the same day continues writing to the latest indexed file.
- Reaching `max_file_size` creates the next indexed file.
- A date change starts index `_0` for the new date.
- `backup_count` limits the total retained files across all dates; the oldest files are deleted first.
- If `{prog_name}` is omitted, the program name is automatically prefixed to prevent different programs from sharing or deleting each other's logs.
- When used, `{prog_name}` must be at the beginning as `{prog_name}_`.
- The format must end with `.log`.

Approximate maximum disk usage per program is `max_file_size * backup_count`.

Do not run multiple independent instances with the same program name, log directory, and `file_name_format` at the same time. Sequential restarts are supported, but concurrent instances cannot coordinate file rotation.

Common formats:

```python
# Recommended: program name + date
file_name_format="{prog_name}_%Y%m%d.log"

# Date only; the program name is added automatically
file_name_format="%Y%m%d.log"

# Program name + year and month; starts a new index sequence each month
file_name_format="{prog_name}_%Y%m.log"

# Program name + date and hour; starts a new index sequence each hour
file_name_format="{prog_name}_%Y%m%d_%H.log"
```

### 3.3 More Examples

See the `example` directory for a complete runnable example.

## 4. 📂 Directory Structure

```text
.
├── example
│   ├── example.py             # Complete usage demonstration
│   ├── module_a
│   │   ├── module_b
│   │   └── worker_ta.py       # Example worker module
│   └── module_c
│       └── worker_tc.py       # Example worker module
├── src
│   └── logging_mp
│       └── __init__.py        # Core library implementation
├── LICENSE
├── pyproject.toml
└── README
```

## 5. 🧠 How It Works

The standard Python `logging` library is **thread-safe**, but it is not designed for **multiprocessing** by default. `logging_mp` uses a queue-based architecture so that multi-threading support is preserved while multi-process logging conflicts are handled centrally:

- **Centralized Listening**: When the main process starts, the library starts a single background **thread** (named `LogListener`) in the main process. This thread is the sole **consumer**: it receives records from the shared queue and performs the Rich console and/or file output in one place. Because the consumer is a thread rather than a spawned child process, it never re-imports `__main__`, so it is immune to import-time crashes silently killing the logger.
- **Transparent Injection**: To keep the user-facing API simple, the library patches `multiprocessing.Process` on import. In `spawn` mode, the log queue is injected during child process bootstrap (`_bootstrap`), so child processes can send logs back immediately after startup.
- **Threads And Processes**:
  - **Threads**: It keeps the thread-safety behavior of the standard `logging` module. Thread logs do not need cross-process communication, so the overhead stays low.
  - **Processes**: In each process, `logger.info()` acts as a **producer** with backpressure: the record goes to the shared queue first, and a producer that outruns the consumer is throttled rather than dropping records. The queue holds at most `queue_size` records in flight, so a producer that outruns the listener is bounded in memory instead of buffering without limit. Under extreme oversubscription a record can still exceed the 3s enqueue deadline; it is then written to stderr, never dropped silently. In the main process the listener thread drains the queue and writes to the console and/or file. Console rendering is delegated to its own asynchronous thread, so a slow or stalled console (e.g. `prog | head`) can never stall the listener and deadlock every producer; under an unusually slow console it degrades to counted, reported drops instead of hanging. The file log is authoritative in normal operation; the cases where it is not are listed below, and each of them reports itself on stderr rather than losing records quietly.
- **Linear Ordering**: Logs from all processes and threads ultimately converge into a single shared queue. The listener processes them in receive order, which avoids interleaved output and multi-process file writing conflicts.
- **Surviving A Killed Producer**: CPython's documented limitation is that a `multiprocessing.Queue` is corrupted if a process is `SIGKILL`ed (e.g. by the OOM killer) while writing to it — the pipe's write lock is a POSIX semaphore with no owner-death recovery, and a partial write desynchronises the reader. The queue cannot be repaired, so the library detects it instead: the listener publishes a heartbeat, and a producer whose previous record has gone unconsumed for a full 5s window reports the stall once and writes to stderr instead. Records already accepted into the queue before that point are lost, so the residue is bounded by the detection window rather than being total, permanent silence.

## 6. ⚠️ Notes

- **Import Order**: In multiprocessing environments using `spawn` mode, ensure that you import `logging_mp` and call `basicConfig` **before** creating any `Process` objects.

- **Start Method**: You may call `multiprocessing.set_start_method()` before or after importing `logging_mp`; the start method is read when each `Process` is constructed, not at import, so either order works. It must, however, come **before** `basicConfig()` — the log queue is created there, and CPython refuses to share a queue built in one context with a process started in another (`A SemLock created in a fork context is being shared with a process in a spawn context`).

- **Windows/macOS**: Because these platforms use `spawn`, **always place process-starting code inside an `if __name__ == "__main__":` block**. Otherwise, recursive startup errors may occur.

- **Process Subclassing**: If you create processes by subclassing `multiprocessing.Process` and override `__init__`, **be sure to call `super().__init__()`**.
- **Shutdown Semantics**: The library shuts down its listener automatically at process exit, registered at import time so that `atexit` handlers you register yourself still get their logs written (`atexit` runs LIFO). Records logged after an explicit shutdown go to stderr rather than into a queue nobody is reading. If the program is terminated abruptly by a signal, `atexit` does not run at all and the last few records are lost.

- **Killed Producers**: If a process is `SIGKILL`ed (`kill -9`, the OOM killer) while it is writing to the log queue, CPython leaves that queue unusable for every process — this is a documented `multiprocessing` limitation, not something a logging library can repair. `logging_mp` detects the stall within 5–10s and falls back to stderr, printing `logging_mp: log queue stopped delivering`. If you see that line, logs after it are on stderr, and the file is complete only up to the kill. (The check is re-evaluated every window, so a queue that was merely stalled — not corrupted — resumes normally and prints `log queue is delivering again`.) The other processes also still **exit**: each one gives up its own undeliverable buffer at exit rather than blocking forever in the queue's feeder thread, which would otherwise hang the parent's `join()` too. Prefer `SIGTERM` for processes that log.

## 7. 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
