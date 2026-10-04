import argparse
import asyncio
import threading

from event_queue import EventBridge
from tracker_pipeline import run_tracker
from llm_worker import LLMWorker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="0")
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--visualize", action="store_true")

    args = parser.parse_args()
    source = int(args.source) if args.source.isdigit() else args.source

    bridge = EventBridge()
    threading.Thread(target=run_tracker, args=(source, bridge, args.model, args.visualize), daemon=True).start()
    asyncio.run(LLMWorker(bridge, description_log_path="descriptions_log.jsonl").run_forever())


if __name__ == "__main__":
    main()