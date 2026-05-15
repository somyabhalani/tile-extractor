import os
import re
import json
import requests
from dotenv import load_dotenv

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-11b-vision-instruct"


def _build_prompt(image_boxes: list, text_blocks: list) -> str:
    """Build a structured prompt for the vision model to scan the full page."""

    img_list = "\n".join([
        f"  - Tile #{i+1}: xref={b['xref']}, region=[x0={b['x0']}, y0={b['y0']}, x1={b['x1']}, y1={b['y1']}]"
        for i, b in enumerate(image_boxes)
    ])

    txt_list = "\n".join([
        f"  - \"{b['text'].replace(chr(10), ' ').strip()}\" at [x0={b['x0']}, y0={b['y0']}, x1={b['x1']}, y1={b['y1']}]"
        for i, b in enumerate(text_blocks)
    ])

    prompt = f"""You are an expert at reading tile/flooring product catalogue pages.

I am giving you the FULL PAGE image of one page from a PDF catalogue. Your job is to scan the ENTIRE PAGE — every corner, every label, every number — and figure out which product text belongs to which tile image.

TILE IMAGES FOUND ON THIS PAGE (with their pixel coordinates on the page):
{img_list if img_list else "  (none found)"}

ALL TEXT FOUND ON THIS PAGE (extracted from the PDF, with coordinates):
{txt_list if txt_list else "  (none found)"}

INSTRUCTIONS:
1. Look at the FULL PAGE image carefully — not just the area immediately around each tile.
2. For each tile listed above, collect ALL related text from anywhere on the page that describes that tile. This includes:
   - Product name or collection name
   - Size / dimensions (e.g. 600x1200, 800x800)
   - Finish type (e.g. Polished, Matt, Satin, Glazed)
   - Surface texture or look (e.g. Marble, Wood, Concrete)
   - SKU code or product code
   - Any price, grade, or technical spec nearby
3. A piece of text "belongs" to a tile if it is: 
   - Visually closest to that tile on the page
   - OR clearly labeling/captioning that tile (above, below, beside it)
   - OR in a panel/section that is dedicated to that tile
4. If a text block appears to be a page header, footer, or irrelevant (like brand name, page number), skip it.
5. If a tile truly has no associated text on the page, return an empty string.

Return ONLY a valid JSON object in this exact format, nothing else:
{{
  "associations": [
    {{
      "xref": <xref number as integer>,
      "text": "<all collected product text for this tile, separated by | character>"
    }}
  ]
}}

IMPORTANT: Output ONLY the JSON. No explanation, no markdown, no code block. Just the raw JSON.
"""
    return prompt


def associate_text_to_tiles(image_b64: str, image_boxes: list, text_blocks: list) -> dict:
    """
    Call NVIDIA NIM vision model to associate text to each tile image.
    Returns a dict: { xref: "matched text" }
    """
    if not NVIDIA_API_KEY:
        raise ValueError("NVIDIA_API_KEY is not set in .env file.")

    if not image_boxes:
        return {}

    # If no text blocks, no point calling the API
    if not text_blocks:
        return {b["xref"]: "" for b in image_boxes}

    prompt = _build_prompt(image_boxes, text_blocks)

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.10,   # Low temp = more consistent structured output
        "top_p": 0.90,
        "stream": False
    }

    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        raw_content = result["choices"][0]["message"]["content"]
        
        # Extract JSON from the model response (model may add extra text)
        json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
        if not json_match:
            print(f"WARNING: Could not find JSON in model response: {raw_content[:200]}")
            return {b["xref"]: "" for b in image_boxes}
        
        parsed = json.loads(json_match.group())
        associations = parsed.get("associations", [])
        
        # Build final dict: { xref -> text }
        result_map = {}
        for item in associations:
            result_map[int(item["xref"])] = item.get("text", "").strip()
        
        return result_map

    except requests.exceptions.Timeout:
        raise RuntimeError("NVIDIA API request timed out. Please try again.")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"NVIDIA API error: {e.response.status_code} - {e.response.text}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse model response as JSON: {e}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error calling NVIDIA API: {e}")
