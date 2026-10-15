import argparse
import asyncio
import threading

import uvicorn

from event_queue import EventBridge
from tracker_pipeline import run_tracker
from llm_worker import LLMWorker
from interface.dashboard import app as dashboard_app


def start_dashboard(port: int):
    config = uvicorn.Config(dashboard_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()  # blocking call; runs its own event loop in this thread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="0")
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--dashboard-port", type=int, default=8000)

    args = parser.parse_args()
    source = int(args.source) if args.source.isdigit() else args.source

    bridge = EventBridge()

    threading.Thread(target=run_tracker, args=(source, bridge, args.model, args.visualize), daemon=True).start()
    threading.Thread(target=start_dashboard, args=(args.dashboard_port,), daemon=True).start()

    print(f"Dashboard: http://localhost:{args.dashboard_port}")

    asyncio.run(LLMWorker(bridge).run_forever())


if __name__ == "__main__":
    main()