import os
import asyncio
import base64
import cv2
from typing import List
from groq import Groq
from openai import OpenAI
from dotenv import load_dotenv

from track_events import TrackEvent

# Load environment variables from .env file
load_dotenv()

MAX_IMAGES_PER_REQUEST = 3
MAX_COMPLETION_TOKENS = 256

client = Groq()

# OpenRouter client retained for switching back if needed:
# openrouter_client = OpenAI(
#     base_url="https://openrouter.ai/api/v1",
#     api_key=os.environ.get("OPENROUTER_API_KEY"),
# )


async def describe_batch_cheap(events: List[TrackEvent]) -> List[str]:
    """
    Send track events to Groq in groups within the model's image limit.
    
    Args:
        events: List of TrackEvent objects containing cropped images to describe
        
    Returns:
        List of description strings, one per event
    """
    descriptions = []
    for start in range(0, len(events), MAX_IMAGES_PER_REQUEST):
        batch = events[start:start + MAX_IMAGES_PER_REQUEST]
        content = [{
            "type": "text",
            "text": (
                f"You're shown {len(batch)} cropped image(s) from a live camera feed. "
                "For each image, in order, give ONE short sentence describing what/who it is "
                "and what they appear to be doing. Reply with exactly one line per image, "
                "no numbering, no extra commentary."
            ),
        }]

        for i, event in enumerate(batch):
            content.append({
                "type": "text",
                "text": f"Image {i + 1} ({event.class_name}, track {event.track_id}):",
            })
            height, width = event.crop.shape[:2]
            scale = min(384 / max(height, width), 1.0)
            if scale < 1.0:
                resized_width = max(1, int(width * scale))
                resized_height = max(1, int(height * scale))
                crop = cv2.resize(
                    event.crop,
                    (resized_width, resized_height),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                crop = event.crop
            _, buf = cv2.imencode(
                ".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70]
            )
            b64 = base64.b64encode(buf).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        def request_descriptions():
            completion = client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[{"role": "user", "content": content}],
                temperature=0.6,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                top_p=0.95,
                reasoning_effort="default",
                stream=True,
                stop=None,
            )
            return "".join(
                chunk.choices[0].delta.content or ""
                for chunk in completion
                if chunk.choices
            )

        # Previous OpenRouter request:
        # response = await asyncio.to_thread(
        #     openrouter_client.chat.completions.create,
        #     model="google/gemma-4-31b-it:free",
        #     messages=[{"role": "user", "content": content}],
        # )
        # text = response.choices[0].message.content.strip()
        text = (await asyncio.to_thread(request_descriptions)).strip()

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        while len(lines) < len(batch):
            lines.append("(no description returned)")
        descriptions.extend(lines[:len(batch)])

    return descriptions