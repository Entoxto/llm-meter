"""Small executors; workers return data, Qt consumes it on its own thread."""
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue


class Workers:
    def __init__(self):
        self.session = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session")
        self.io = ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio-io")
        self.events = SimpleQueue()

    def submit(self, name, function, session=False):
        def run():
            try:
                self.events.put(("done", {"name": name, "value": function()}))
            except Exception as exc:
                self.events.put(("failed", {"name": name, "message": str(exc)}))
        return (self.session if session else self.io).submit(run)

    def shutdown(self):
        self.session.shutdown(wait=False, cancel_futures=True)
        self.io.shutdown(wait=False, cancel_futures=True)
