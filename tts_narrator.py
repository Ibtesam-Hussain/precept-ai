"""
Realtime voice narration. Runs pyttsx3 on its own thread, consuming a queue,
so speech never blocks the LLM worker or the tracker.
"""

import queue
import threading
import pyttsx3

_text_queue: "queue.Queue[str]" = queue.Queue()


def _run():
    engine = pyttsx3.init()
    engine.setProperty("rate", 175)  # tune to taste
    while True:
        text = _text_queue.get()
        if text is None:
            break
        engine.say(text)
        engine.runAndWait()


def start():
    threading.Thread(target=_run, daemon=True).start()


def speak(text: str) -> None:
    _text_queue.put_nowait(text)