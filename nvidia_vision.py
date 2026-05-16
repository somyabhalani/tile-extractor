import os
import requests
import json
import re

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-11b-vision-instruct"

def scan_full_page_products(image_b64: str) -> dict:
    """
    Sends a high-res full page image to NVIDIA to extract structured product info.
    Uses the user's specific expert prompt for high-precision matching.
    """
    if not NVIDIA_API_KEY:
        return {"products": []}

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json"
    }

    prompt = """You are a tile catalogue data extraction expert.

I will give you an image of a tile catalogue page.

Your job is to identify every distinct tile PRODUCT on the page and extract its information.

Rules:
- A product is identified by its NAME (e.g. "CATRIA ASH MT", "ROCKSTONE NERO" etc)
- Each product may have multiple tile images shown (different sizes/face variants) - they all belong to the same product
- The spec text (size, thickness, surface, faces) near a tile belongs to that tile's product
- If multiple tiles share the same spec block, they are the same product
- Ignore QR codes, logos, page numbers, decorative elements

For each product return:
- name: full product name as written
- collection: brand/collection name if shown (e.g. "ROCKSTONE")
- size: tile dimensions (e.g. "600x600mm")
- thickness: (e.g. "12mm")
- surface: finish type (e.g. "Matt", "Polished", "Glossy")
- faces: number of faces as integer
- position: where is the PRIMARY large tile on the page? (top-left / top-right / bottom-left / bottom-right / center / full-page)
- image_description: briefly describe the tile appearance (color, texture, pattern) in 1 sentence

Return ONLY valid JSON, no explanation, no markdown, no preamble:

{
  "page_number": <if visible, else null>,
  "products": [
    {
      "name": "",
      "collection": "",
      "size": "",
      "thickness": "",
      "surface": "",
      "faces": 0,
      "position": "",
      "image_description": ""
    }
  ]
}

If a field is not visible on the page use null.
If you are uncertain about any value add a "confidence": "low" field to that product object.
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

    print(f"INFO: Sending full page to NVIDIA (Expert Prompt)...")
    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # Clean up markdown if AI includes it despite instructions
        content = re.sub(r'```json\s*|\s*```', '', content).strip()
        
        return json.loads(content)
    except Exception as e:
        print(f"ERROR: AI Extraction failed: {e}")
        return {"products": []}
