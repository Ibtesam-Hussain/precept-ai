import os
import asyncio
import base64
import cv2
from typing import List
from openai import OpenAI
from dotenv import load_dotenv

from track_events import TrackEvent

# Load environment variables from .env file
load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)


async def describe_batch_cheap(events: List[TrackEvent]) -> List[str]:
    """
    Send a batch of track events to OpenRouter API for image description.
    
    Args:
        events: List of TrackEvent objects containing cropped images to describe
        
    Returns:
        List of description strings, one per event
    """
    content = [{
        "type": "text",
        "text": (
            f"You're shown {len(events)} cropped image(s) from a live camera feed. "
            "For each image, in order, give ONE short sentence describing what/who it is "
            "and what they appear to be doing. Reply with exactly one line per image, "
            "no numbering, no extra commentary."
        ),
    }]

    for i, e in enumerate(events):
        content.append({"type": "text", "text": f"Image {i + 1} ({e.class_name}, track {e.track_id}):"})
        height, width = e.crop.shape[:2]
        scale = min(384 / max(height, width), 1.0)
        if scale < 1.0:
            resized_width = max(1, int(width * scale))
            resized_height = max(1, int(height * scale))
            crop = cv2.resize(e.crop, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
        else:
            crop = e.crop
        _, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        b64 = base64.b64encode(buf).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    response = await asyncio.to_thread(
        client.chat.completions.create,
        # model="qwen/qwen3.8-27b",  #this qwen model got paid
        model="google/gemma-4-31b-it:free",  # this gemma model is free
        messages=[{"role": "user", "content": content}],
    )

    text = response.choices[0].message.content.strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    while len(lines) < len(events):
        lines.append("(no description returned)")
    return lines[:len(events)]