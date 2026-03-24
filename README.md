<div align="center">
  <div align="center">
    <img src="./logging_mp.png" width="45%" style="vertical-align: middle;">
    <span style="color: #ddd; margin: 0 20px; font-size: 30px; vertical-align: middle;">|</span>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
    <a href="https://www.unitree.com/" target="_blank" style="vertical-align: middle; margin-left: 30px;">
      <img src="https://www.unitree.com/images/0079f8938336436e955ea3a98c4e1e59.svg" width="45%">
    </a>
  </div>
  <br>
  <p align="center">
    <a>English</a> | <a href="README_zh-CN.md">中文</a>
  </p>
</div>


**logging_mp** is a Python library specifically designed for **multiprocessing support** in logging. 

It solves the common logging problems in multiprocessing environments, especially interleaved output and file writing conflicts. In `spawn` mode, `logging_mp` uses **Monkey Patch** technology to connect child processes to a central logging queue automatically.

## 1. ✨ Features

* ⚡ **Zero-Config Multiprocessing:** Child processes automatically send logs to the main process. No need to pass `Queue` objects manually.
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
# In spawn mode, this automatically starts the listener process and applies the required monkey patch.
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
| `backup_count` | `int` | `10` | Maximum number of timestamped log files to keep. |
| `max_file_size` | `int` | `100*1024*1024` | Maximum size in bytes of a single timestamped log file. Once exceeded, logging continues in a new timestamped file like `example_20260324_153000_123456.log`. |

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

- **Centralized Listening**: When the main process starts, the library creates a dedicated background process named `_logging_mp_queue_listener`. This single **consumer** receives records from the queue and performs Rich console output or file writing in one place.
- **Transparent Injection**: To keep the user-facing API simple, the library patches `multiprocessing.Process` on import. In `spawn` mode, the log queue is injected during child process bootstrap (`_bootstrap`), so child processes can send logs back immediately after startup.
- **Threads And Processes**:
  - **Threads**: It keeps the thread-safety behavior of the standard `logging` module. Thread logs do not need cross-process communication, so the overhead stays low.
  - **Processes**: In each child process, `logger.info()` acts as a **producer**. Records are sent to a cross-process queue first, while console output and file I/O are handled by the listener process. This greatly reduces logging-related blocking in normal use, though it is not a strict zero-blocking system.
- **Linear Ordering**: Logs from all processes and threads ultimately converge into a single in-memory queue. The listener processes them in receive order, which avoids interleaved output and multi-process file writing conflicts.

## 6. ⚠️ Notes

- **Import Order**: In multiprocessing environments using `spawn` mode, ensure that you import `logging_mp` and call `basicConfig` **before** creating any `Process` objects.

- **Windows/macOS**: Because these platforms use `spawn`, **always place process-starting code inside an `if __name__ == "__main__":` block**. Otherwise, recursive startup errors may occur.

- **Process Subclassing**: If you create processes by subclassing `multiprocessing.Process` and override `__init__`, **be sure to call `super().__init__()`**.
- **Shutdown Semantics**: The library shuts down its listener automatically at process exit. If the program is terminated abruptly, the last few log records may still be lost.

## 7. 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.