import os
import requests
import json
import re

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-11b-vision-instruct"

def scan_full_page_products(image_b64: str) -> list:
    """
    Sends a high-res full page image to NVIDIA to extract all products as a structured list.
    """
    if not NVIDIA_API_KEY:
        return []

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json"
    }

    prompt = """You are a professional tile catalogue data extractor.
Look at this entire page and identify EVERY tile product shown.

Return the data as a JSON ARRAY of objects. Each object MUST have these fields:
- "name": Product Name (SKU/Code)
- "size": Size (e.g., 600x600mm)
- "finish": Finish/Surface (e.g., Polished, Matt)
- "faces": Number of Faces
- "thickness": Thickness

RULES:
- If multiple images represent the SAME product (e.g., different faces), group them as ONE entry.
- ONLY return the JSON array. No conversational text.
"""

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.1
    }

    print(f"INFO: Sending full page (11B Vision) to NVIDIA API for JSON...")
    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # Clean up markdown if AI includes it
        content = re.sub(r'```json\s*|\s*```', '', content).strip()
        
        return json.loads(content)
    except Exception as e:
        print(f"ERROR: AI JSON extraction failed: {e}")
        return []
