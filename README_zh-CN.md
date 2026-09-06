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


**logging_mp** 是一个专为 Python 设计的**支持多进程**的日志库。

它解决了标准 [logging](https://docs.python.org/zh-cn/3.14/library/logging.html) 模块在多进程环境下的丢失问题。在 `spawn` 模式下，`logging_mp` 通过 **Monkey Patch** 技术，自动把子进程接入中心日志队列。

## 1. ✨ 核心特性

* **⚡ 支持多进程 & 多线程**：完全兼容 Python 的 `threading` 模块。无需复杂配置，子进程自动继承日志能力。
* **💻 跨平台兼容**：支持 Linux ( `fork` ) 以及 Windows/macOS ( `spawn` ) 的启动方式。
* **🎨 Rich 终端美化**：集成 [Rich](https://github.com/Textualize/rich) 库，提供高亮、清晰的控制台日志输出。
* **📂 文件管理**：自动启动后台监听线程，将所有子进程和线程的日志汇聚到带时间戳的日志文件中，并支持按大小轮转、按数量清理。
* **🔒 线程安全**：完全兼容 Python 的 `threading` 模块。

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
# 🔥 必须在创建任何进程以及子模块前调用 basicConfig
# 在spawn模式下，会自动把子进程接入日志队列（Monkey Patch）并启动后台监听线程
logging_mp.basicConfig(
    level=logging_mp.INFO, 
    console=True, 
    file=True, 
    file_path="logs",
    backup_count=10,
    max_file_size=100 * 1024 * 1024
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
| `backup_count` | `int` | `10` | 当前程序最多保留的日志文件总数，超过后优先删除最旧文件。 |
| `max_file_size` | `int` | `100*1024*1024` | 单个日志文件允许的最大字节数，达到后创建新文件继续写入。 |
| `file_name_format` | `str` | `None` | 可选文件名格式，支持 `strftime` 格式和 `{prog_name}`，详细行为见下文。 |
| `queue_size` | `int` | `65536` | 队列中最多允许多少条日志在途，超出即对生产者施加背压，从而限定内存占用。会被平台信号量上限截断（macOS 为 32767）；仅首次调用生效。 |

#### 文件命名与轮转

不设置 `file_name_format` 时，每次启动以及文件大小轮转都会创建时间戳文件：

```text
example_20260324_153000_123456.log
```

设置 `file_name_format="{prog_name}_%Y%m%d.log"` 后，日志按日期和递增编号命名：

```text
example_20260324_0.log
example_20260324_1.log
example_20260325_0.log
```

- 同一天再次启动时，继续写入当天编号最大的文件。
- 当前文件达到 `max_file_size` 后，创建下一个编号文件。
- 日期变化后，新日期从 `_0` 开始。
- `backup_count` 限制所有日期的日志文件总数，超过后优先删除最旧文件。
- 文件名格式中省略 `{prog_name}` 时，会自动添加程序名前缀，避免不同程序共用或误删日志。
- 使用 `{prog_name}` 时，必须以 `{prog_name}_` 开头。
- 文件名格式必须以 `.log` 结尾。

每个程序的日志磁盘占用上限约为 `max_file_size * backup_count`。

不要同时运行多个使用相同程序名、日志目录和 `file_name_format` 的独立程序实例。支持顺序重启，但并发实例之间无法协调文件轮转。

常用格式：

```python
# 推荐：程序名 + 日期
file_name_format="{prog_name}_%Y%m%d.log"

# 仅使用日期；程序名会自动添加
file_name_format="%Y%m%d.log"

# 程序名 + 年月；每月重新从 _0 开始编号
file_name_format="{prog_name}_%Y%m.log"

# 程序名 + 日期和小时；每小时重新从 _0 开始编号
file_name_format="{prog_name}_%Y%m%d_%H.log"
```

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

- **中心化监听 (Aggregation)**： 在主进程启动时，系统会在主进程内自动创建一个后台线程 `LogListener`，作为全局唯一的**消费者**，从共享队列中提取日志，并统一执行 Rich 控制台渲染或文件写入。由于消费者是线程（而非独立的子进程），它不会重入导入 `__main__`，因此不会因导入期崩溃而静默吞掉日志。
- **透明注入 (Monkey Patch)**： 为了实现用户**零感知**接入，库在导入时会修补 `multiprocessing.Process`。在 `spawn` 模式下，当执行 `Process.start()` 时，系统会自动将日志队列对象注入到子进程的引导阶段 (`_bootstrap`)，确保子进程启动瞬间即具备日志回传能力。
- **全场景支持 (Threads & Processes)**：
  - **多线程**：直接继承原生 `logging` 的线程安全特性，线程间日志无需跨进程通讯，开销极低。
  - **多进程**：各进程中的 `logger.info()` 扮演**生产者**角色并施加背压：日志先写入共享队列，生产过快时会被限速而不会丢记录；主进程内的监听线程再统一输出到控制台和/或文件。控制台渲染被委派给独立的异步线程，因此缓慢或卡死的控制台（如 `prog | head`）不会拖住监听线程、进而卡死所有生产者；当控制台异常缓慢时，会降级为"计数并报告丢弃"而不是挂起。队列最多容纳 `queue_size` 条在途日志，生产速度长期超过消费速度时内存有界，而不会无限堆积；极端超订阅下单条记录可能超过 3 秒入队期限，此时该记录写入 stderr，绝不静默丢弃。正常情况下文件日志是权威记录，少数例外见下方注意事项，且每一种例外都会在 stderr 上自报，而不是悄悄丢日志。
- **生产者被杀后的自救 (Killed Producer)**： CPython 有一个已知限制：进程在写队列的过程中被 `SIGKILL`（例如被 OOM killer 干掉）会损坏这个 `multiprocessing.Queue`——管道写锁是 POSIX 信号量，没有持有者死亡后的恢复机制，且半截写入会让读端错位。队列无法修复，因此本库改为“检测”：监听线程对外发布心跳，生产者若发现自己上一条记录经过整整 5 秒窗口仍未被消费，就报告一次并改写 stderr。在此之前已被队列接收的记录会丢失，也就是说残留量被检测窗口限定，而不再是全量、永久的静默丢失。
- **线性顺序保证 (Ordering)**： 所有进程与线程的日志最终都会汇聚到同一个共享队列。监听线程按接收到的先后顺序处理，确保了输出在时间轴上的线性一致性，彻底杜绝了多进程/多线程同时写文件导致的字符交织和文件锁死问题。

## 6. ⚠️ 注意事项

- **导入顺序**：在 `spawn` 模式的多进程下，请确保在创建任何 `Process` 对象之前导入 `logging_mp` 并调用 `basicConfig`。

- **启动方式 (start method)**：`multiprocessing.set_start_method()` 放在 `import logging_mp` 之前或之后都可以——启动方式是在每次构造 `Process` 时才读取的，而不是在 import 时确定。但它必须放在 `basicConfig()` **之前**：日志队列在 `basicConfig()` 里创建，CPython 不允许把一种上下文中创建的队列交给另一种上下文启动的进程（会抛 `A SemLock created in a fork context is being shared with a process in a spawn context`）。

- **Windows/macOS 用户**：由于使用 `spawn` 启动模式，请务必将启动代码放在 `if __name__ == "__main__":` 块中，否则可能会导致递归启动错误。

- **Process 子类化**：如果您通过继承 `multiprocessing.Process` 类来创建进程，且重写了 `__init__`，请确保调用 `super().__init__()`。
- **退出行为**：库会在进程退出时自动关闭监听线程并排空剩余日志。该钩子在 import 时注册，`atexit` 按 LIFO 执行，因此你自己注册的 `atexit` 处理函数打的日志仍能被写出。显式关闭之后再打的日志会走 stderr，而不是进入无人读取的队列。如果程序被信号强制终止，`atexit` 根本不会执行，最后少量日志仍会丢失。

- **生产者被 `kill -9`**：进程在写日志队列时被 `SIGKILL`（`kill -9`、OOM killer）会让该队列对所有进程都不可用，这是 `multiprocessing` 的既有限制，日志库无法修复。`logging_mp` 会在 5~10 秒内检测到并回退到 stderr，打印 `logging_mp: log queue stopped delivering`。看到这行，说明其后的日志都在 stderr 上，文件只完整到被杀的那一刻。（该判定每个窗口重新评估，因此只是一时卡顿而未损坏的队列会自行恢复，并打印 `log queue is delivering again`。）其余进程也仍然**能退出**：每个进程在退出时会放弃自己那份再也送不出去的缓冲，而不是永久阻塞在队列的 feeder 线程里——否则父进程的 `join()` 也会跟着挂死。对会打日志的进程，优先使用 `SIGTERM`。

## 7. 📄 开源协议

本项目基于 MIT 协议开源 - 详见 [LICENSE](LICENSE) 文件。
