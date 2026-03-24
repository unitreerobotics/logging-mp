import multiprocessing
import threading
# try:
#     multiprocessing.set_start_method('spawn', force=True)
# except RuntimeError:
#     pass

import logging_mp
# Note: basicConfig must be called first to set up the logging system before any loggers are created
logging_mp.basicConfig(level=logging_mp.WARNING, file=True, backup_count=5, max_file_size=1024 * 1024)
logger = logging_mp.getLogger(__name__)

from module_a.worker_ta import worker_ta
from module_a.module_b.worker_tb import worker_tb
from module_c.worker_tc import worker_tc

def main():
    try:
        logger.debug("M: Should not be printed.")
        logger.info("M: Should not be printed.")
        logger.warning("M: This is a warning message")
        logger.error("M: This is an error message")
        logger.critical("M: Now below starting Process workers")

        processes = []
        for i, target in enumerate([worker_ta, worker_tb, worker_tc]):
            p = multiprocessing.Process(target=target, name=f"Process-{i}")
            p.start()
            processes.append(p)
        for p in processes:
            p.join()

        logger.critical("M: Now below starting Thread workers")

        threads = []
        for i, target in enumerate([worker_ta, worker_tb, worker_tc]):
            t = threading.Thread(target=target, name=f"Thread-{i}")
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        
        logger.critical("M: All workers have completed.")

    except KeyboardInterrupt:
        logger.error("KeyboardInterrupt received, stopping processes.")

if __name__ == "__main__":
    main()