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


**logging_mp** 是一个专为 Python 设计的**支持多进程**的日志库。

它解决了标准 [logging](https://docs.python.org/zh-cn/3.14/library/logging.html) 模块在多进程环境下日志错乱、丢失或死锁的问题。在 `spawn` 模式下，`logging_mp` 通过 **Monkey Patch** 技术，自动处理进程间的 `Queue` 传递与监听。

## 1. ✨ 核心特性

* **⚡ 支持多进程 & 多线程**：完全兼容 Python 的 `threading` 模块。无需复杂配置，子进程自动继承日志能力。
* **💻 跨平台兼容**：支持 Linux ( `fork` ) 以及 Windows/macOS ( `spawn` ) 的启动方式。
* **🎨 Rich 终端美化**：集成 [Rich](https://github.com/Textualize/rich) 库，提供高亮、清晰的控制台日志输出。
* **📂 文件管理**：自动启动后台监听进程，将所有子进程和线程的日志汇聚到同一个文件中，并支持自动轮转。

## 2. 🛠️ 安装说明

### 2.1 源码

```bash
git clone https://github.com/silencht/logging-mp
cd logging_mp
pip install -e .
```

### 2.2 PyPI

```bash
pip install logging-mp
```

## 3. 🚀 快速开始

使用 `logging_mp` 几乎与使用标准库一样简单。您只需要在**主进程入口**进行一次初始化配置。

### 3.1 基础示例

```python
import multiprocessing
import time

import logging_mp
# 必须在创建任何进程以及子模块前调用 basicConfig
# 在spawn模式下，会自动启动日志监听进程并注入 Monkey Patch
logging_mp.basicConfig(
    level=logging_mp.INFO, 
    console=True, 
    file=True, 
    file_path="logs"
)
# 获取 Logger
logger_mp = logging_mp.getLogger(__name__)

def worker_task(name):
    # 子进程中直接获取 logger，无需任何额外配置
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

### 3.2 配置参数

`logging_mp.basicConfig` 支持以下参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `level` | `int` | `logging_mp.WARNING` | 全局日志级别 (例如 `INFO`, `DEBUG`) |
| `console` | `bool` | `True` | 是否启用终端输出 |
| `file` | `bool` | `False` | 是否启用文件写入 |
| `file_path` | `str` | `"logs"` | 日志文件存放目录 |
| `backupCount` | `int` | `10` | 保留的历史日志文件数量 (按次轮转) |

### 3.3 更多示例

详见 example 目录

## 4. 📂 项目结构

```text
.
├── example
│   ├── example.py             # 完整的使用示例代码
│   ├── module_a
│   │   ├── module_b
│   │   └── worker_ta.py       # 模块化调用示例
│   └── module_c
│       └── worker_tc.py       # 模块化调用示例
├── src
│   └── logging_mp
│       └── __init__.py        # 核心库源码
├── LICENSE
├── pyproject.toml
└── README
```

## 5. 🧠 工作原理

原生 Python `logging` 库虽然是**线程安全**的，但并不支持**多进程模式**。`logging_mp` 采用了一套异步通信机制，在保持多线程兼容性的同时，彻底解决了多进程并发写入的冲突问题：

- **中心化监听 (Aggregation)**： 在主进程启动时，系统会自动创建一个独立的后台进程 `_logging_mp_queue_listener`。它是全局唯一的**消费者**，负责从队列中提取日志，并统一执行 Rich 控制台渲染或文件写入操作。
- **透明注入 (Monkey Patch)**： 为了实现用户**零感知**接入，库在导入时会修补 `multiprocessing.Process`。在 `spawn` 模式下，当执行 `Process.start()` 时，系统会自动将日志队列对象注入到子进程的引导阶段 (`_bootstrap`)，确保子进程启动瞬间即具备日志回传能力。
- **全场景支持 (Threads & Processes)**：
  - **多线程**：直接继承原生 `logging` 的线程安全特性，线程间日志无需跨进程通讯，开销极低。
  - **多进程**：各子进程中的 `logger.info()` 扮演**生产者**角色。日志记录被推入跨进程队列后立即返回。由于耗时的磁盘 I/O 在监听进程中异步完成，您的业务逻辑几乎不会被日志操作阻塞。
- **线性顺序保证 (Ordering)**： 所有进程与线程的日志最终都会汇聚到同一个内存队列。监听进程按接收到的先后顺序处理，确保了输出在时间轴上的线性一致性，彻底杜绝了多进程/多线程同时写文件导致的字符交织和文件锁死问题。

## 6. ⚠️ 注意事项

- **导入顺序**：在 `spawn` 模式的多进程下，请确保在创建任何 `Process` 对象之前导入 `logging_mp` 并调用 `basicConfig`。

- **Windows/macOS 用户**：由于使用 `spawn` 启动模式，请务必将启动代码放在 `if __name__ == "__main__":` 块中，否则可能会导致递归启动错误。

- **Process 子类化**：如果您通过继承 `multiprocessing.Process` 类来创建进程，且重写了 `__init__`，请确保调用 `super().__init__()`，否则日志队列可能无法正确注入。

## 7. 📄 开源协议

本项目基于 MIT 协议开源 - 详见 [LICENSE](https://www.google.com/search?q=LICENSE) 文件。
