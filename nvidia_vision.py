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
- coordinates: estimate the exact [center_x, center_y] coordinates of the PRIMARY large tile on the page as percentages from 0 to 100. (e.g. [25, 25] is top-left, [75, 75] is bottom-right).
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
      "coordinates": [0, 0],
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
        content = result['choices'][0]['message']['content'].strip()

        if not content:
            print("WARN: Empty response from model, falling back to text mode...")
            return _fallback_text_scan(image_b64, headers)

        # Clean up markdown fences if AI includes them despite instructions
        content = re.sub(r'```json\s*|\s*```', '', content).strip()

        # Try to extract JSON even if there is preamble text before/after it
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group(0))

        print("WARN: No JSON found in response, falling back to text mode...")
        return _fallback_text_scan(image_b64, headers)

    except Exception as e:
        print(f"ERROR: AI Extraction failed: {e}")
        return {"products": []}


def _fallback_text_scan(image_b64: str, headers: dict) -> dict:
    """Fallback: Ask the model for plain text if JSON fails."""
    fallback_prompt = """You are a tile catalogue data extraction expert.
Look at this catalogue page and list every distinct tile product.

For each product write:
- Product Name
- Collection
- Size
- Thickness
- Surface/Finish
- Number of Faces
- Position on page
- Coordinates (e.g., [25, 25] for top-left in percentages)
- Brief description of the tile appearance

Be direct and concise. List all products you can see."""

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": fallback_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.2
    }
    print("INFO: Running fallback plain-text scan...")
    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content'].strip()
        return {"products": [], "raw_text": content}
    except Exception as e:
        print(f"ERROR: Fallback also failed: {e}")
        return {"products": [], "raw_text": f"Error: {str(e)}"}

