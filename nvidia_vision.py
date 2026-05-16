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

CRITICAL RULES:
1. ONLY count items that are actual TILE PRODUCTS with technical specifications.
2. IGNORE Brand Logos, Collection Headers (e.g., "ROCKSTONE COLLECTION"), and page titles.
3. A product MUST have at least a Name and a Size (e.g., 600x600mm) associated with it in the nearby text.
4. If you see a header like "DURAGRES" or "ROCKSTONE" with no specs below it, SKIP IT completely.
5. DO NOT hallucinate. If you see 4 physical tile images, your JSON must contain exactly 4 products.
6. NO INTRO OR OUTRO. Just the JSON.

NEGATIVE CONSTRAINTS:
- NEVER list a brand name as a product.
- NEVER list a collection title as a product.
- NEVER count the same tile twice if it's just shown in a different lifestyle room-setting.

For each product return:
- name: full product name (e.g. "CATRIA ASH MT")
- size: dimensions (e.g. "600x600mm")
- thickness: e.g. "12mm"
- surface: finish type
- faces: number of faces
- position: primary location on page
- image_description: 1-sentence description

Return ONLY valid JSON:
{
  "page_number": null,
  "products": [
    {
      "name": "",
      "size": "",
      "thickness": "",
      "surface": "",
      "faces": 0,
      "position": "",
      "image_description": ""
    }
  ]
}
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

