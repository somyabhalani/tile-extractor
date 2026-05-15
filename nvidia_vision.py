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
    """Build a structured prompt for the vision model."""
    
    img_list = "\n".join([
        f"  - Image #{i+1}: xref={b['xref']}, position=[x0={b['x0']}, y0={b['y0']}, x1={b['x1']}, y1={b['y1']}]"
        for i, b in enumerate(image_boxes)
    ])
    
    txt_list = "\n".join([
        f"  - Text Block #{i+1}: \"{b['text'].replace(chr(10), ' ')}\" at [x0={b['x0']}, y0={b['y0']}, x1={b['x1']}, y1={b['y1']}]"
        for i, b in enumerate(text_blocks)
    ])
    
    prompt = f"""You are analyzing a page from a tile/flooring product catalogue PDF.

I have extracted the following tile images and text blocks from this page, each with their pixel coordinates.

TILE IMAGES ON THIS PAGE:
{img_list if img_list else "  (none found)"}

TEXT BLOCKS ON THIS PAGE:
{txt_list if txt_list else "  (none found)"}

YOUR TASK:
Look at the page image I am providing. For each tile image listed above, find the text that is visually closest to it or clearly labeling it (product name, size, finish, SKU, code, etc.). 

Return ONLY a valid JSON object in this exact format, with no extra explanation:
{{
  "associations": [
    {{
      "xref": <xref number>,
      "text": "<the matched product text for this tile, or empty string if no text found>"
    }}
  ]
}}

Rules:
- Only include text that clearly belongs to that specific tile (nearby label, caption, spec).
- If a tile has no associated text, return an empty string for "text".
- Do not combine text from different tiles.
- Output ONLY the JSON, nothing else.
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
