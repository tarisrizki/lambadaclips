import concurrent.futures
import multiprocessing

# CPU Executor for heavy tasks (ffmpeg, model inference)
# Limit to the number of CPU cores, minimum 2.
CPU_CORES = multiprocessing.cpu_count()
cpu_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(2, CPU_CORES),
    thread_name_prefix="cpu_worker"
)

# IO Executor for network requests and file I/O
# Can have a higher number of workers since they are I/O bound.
io_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=min(32, (CPU_CORES or 1) + 4),
    thread_name_prefix="io_worker"
)
