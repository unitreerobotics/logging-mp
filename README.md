<div align="center">
  
<div style="display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 20px;">
  <img src="./logging_mp.png" style="width: 45%; min-width: 250px; max-width: 300px;">
  <span style="color: #ddd; font-size: 30px;">        </span>
  <a href="https://www.unitree.com/">
    <img src="https://www.unitree.com/images/0079f8938336436e955ea3a98c4e1e59.svg" style="width: 45%; min-width: 250px; max-width: 300px;">
  </a>
</div>

<br>
<a>English</a> | <a href="README_zh-CN.md">中文</a>
</div>

**logging_mp** is a Python library specifically designed for **multiprocessing support** in logging. 

It solves the issues of log disorder, loss, or deadlock that arise with the standard [logging](https://docs.python.org/3/library/logging.html) module in multiprocessing environments. In `spawn` mode, `logging_mp` automatically handles inter-process `Queue` transmission and monitoring through **Monkey Patch** technology.

## 1. ✨ Features

* ⚡ **Support Multi-Processing & Thread:** Fully compatible with `threading` modules. Child processes automatically send logs to the main process.
* 💻 **Cross-Platform Support:** Works seamlessly with both `fork` (Linux) and `spawn` (Windows/macOS) start methods.
* 🎨 **Rich Integration:** Beautiful, colorized console output powered by [Rich](https://github.com/Textualize/rich).
* 📂 **File Logging:** Aggregates logs from all processes and threads into a single, rotated log file.

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

Using `logging_mp` is nearly identical to using the standard logging module, but with multiprocessing superpowers.

### 3.1 The Setup

In your entry point script (e.g., `main.py`), initialize the system **before** creating any processes.

```python
import multiprocessing
import time

import logging_mp
# basicConfig must be called before creating any processes or submodules.
# In spawn mode, this automatically starts the log listening process and injects Monkey Patches.
logging_mp.basicConfig(
    level=logging_mp.INFO, 
    console=True, 
    file=True,
    file_path="logs"
)
# Get a logger
logger_mp = logging_mp.getLogger(__name__)

def worker_task(name):
    # In the child process, just get a logger and log!
    # No queues to configure, no listeners to start.
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
| `backupCount` | `int` | `10` | Number of previous session logs to keep. |

### 3.3 More Examples

For details, please refer to the example directory.

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

The native Python `logging` library, while **thread-safe**, does not support **multiprocessing mode**. `logging_mp` employs an asynchronous communication mechanism that, while maintaining multi-threading compatibility, thoroughly resolves the conflicts caused by concurrent writes in multiprocessing environments:

- **Centralized Listening (Aggregation)**: Upon main process startup, the system automatically creates a separate background process `_logging_mp_queue_listener`. This globally unique **"consumer"** is responsible for extracting logs from the queue and uniformly performing Rich console rendering or file writing operations.
- **Transparent Injection (Monkey Patch)**: To achieve "zero-perception" user integration, the library patches `multiprocessing.Process` upon import. In `spawn` mode, when `Process.start()` is executed, the system automatically injects the log queue object into the child process's bootstrapping phase (`_bootstrap`), ensuring the child process gains log-return capability instantly upon startup.
- **Full-Scenario Support (Threads & Processes)**:
  - **Multi-threading**: Directly inherits the thread-safety features of native `logging`. Logs between threads do not require cross-process communication, resulting in minimal overhead.
  - **Multiprocessing**: Within each child process, `logger.info()` acts as a **"producer"**. Log entries are pushed into a cross-process queue and return immediately. Since time-consuming disk I/O is performed asynchronously in the listener process, your business logic is hardly blocked by logging operations.
- **Linear Order Guarantee (Ordering)**: Logs from all processes and threads ultimately converge into a single in-memory queue. The listener processes them in the order they are received, ensuring linear consistency in the output timeline and completely eliminating issues such as interleaved characters or file deadlocks caused by simultaneous writes from multiple processes or threads.

## 6. ⚠️ Compatibility Note

- **Import Order**: In multiprocessing environments using `spawn` mode, ensure that you import `logging_mp` and call `basicConfig` **before** creating any `Process` objects.

- **Windows/macOS Users**: Due to the use of `spawn` startup mode, **always place the startup code inside an `if __name__ == "__main__":` block**. Otherwise, it may cause recursive startup errors.

- **Process Subclassing**: If you create processes by subclassing `multiprocessing.Process` and override the `__init__` method, **be sure to call `super().__init__()`**. Otherwise, the logging queue may not be properly injected.

## 7. 📄 License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.
