import os
import re
import json
import requests
from dotenv import load_dotenv

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-90b-vision-instruct"


def _scan_single_tile(tile_box: dict, headers: dict) -> dict:
    """Scan a single cropped tile image to extract its specs."""
    crop_b64 = tile_box.get("crop_b64")
    if not crop_b64:
        return {"xref": tile_box["xref"], "text": ""}
        
    prompt = """You are a strict data extraction robot.
Look at the provided image crop of a tile and its text label.
Extract the product details.

CRITICAL RULES:
1. NEVER use conversational language. DO NOT say "The image shows" or "Based on the image".
2. If a detail is not visible, leave it blank. Do not explain that it is missing.
3. Your entire response MUST be ONLY a valid JSON object.

Required JSON format:
{
  "Product": "name here or blank",
  "Size": "size here or blank",
  "Finish": "finish here or blank",
  "SKU": "code here or blank",
  "Faces": "faces here or blank"
}"""

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{crop_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        "max_tokens": 256,
        "temperature": 0.0,
        "top_p": 0.90,
        "stream": False
    }

    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        
        result = response.json()
        raw_content = result["choices"][0]["message"]["content"]
        
        # Try JSON parsing
        json_match = re.search(r'\{[\s\S]*\}', raw_content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                # Combine non-empty values
                vals = [str(v).strip() for v in parsed.values() if v and str(v).strip() and str(v).lower() not in ('n/a', 'none', 'blank', 'not provided', 'not specified')]
                text = " | ".join(vals)
                return {"xref": tile_box["xref"], "text": text}
            except:
                pass
            
        # Fallback raw extraction
        lines = []
        for line in raw_content.split('\n'):
            line = line.strip()
            # Ignore conversational fluff
            lower = line.lower()
            if any(x in lower for x in ["the image", "based on", "not provided", "not explicitly", "not specified", "is described", "could indicate"]):
                continue
            # Remove markdown bullets
            line = re.sub(r'^[\*\-]\s*', '', line)
            line = re.sub(r'[\{\}"]', '', line)
            if line and len(line) > 1 and ":" in line:
                val = line.split(":", 1)[1].strip()
                if val and val.lower() not in ('n/a', 'none', 'blank', 'not provided', 'not specified'):
                    lines.append(val)
            elif line and len(line) > 1 and " | " in line:
                lines.append(line)
                
        text = " | ".join(lines)
        return {"xref": tile_box["xref"], "text": text}
        
    except Exception as e:
        print(f"ERROR: Failed to scan tile xref {tile_box['xref']}: {e}")
        return {"xref": tile_box["xref"], "text": ""}

def associate_text_to_tiles(image_b64: str, image_boxes: list, text_blocks: list) -> dict:
    """
    Call NVIDIA NIM vision model using isolated cropped images for 100% precision.
    Uses concurrent execution to scan all tiles on the page simultaneously.
    Returns a dict: { xref: "matched text" }
    """
    import concurrent.futures
    
    # Read dynamically to pick up Render environment variables
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("NVIDIA_API_KEY environment variable is not set. Please add it to your Render Environment Variables.")

    if not image_boxes:
        return {}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }

    print(f"INFO: Concurrently scanning {len(image_boxes)} isolated tile crops via NVIDIA 90B API...")
    
    result_map = {}
    
    # Process all crops concurrently but with low workers to avoid API rate limits/timeouts
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_scan_single_tile, box, headers) for box in image_boxes]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res and res.get("text"):
                result_map[int(res["xref"])] = res["text"]
                
    print(f"INFO: Successfully extracted precision text for {len(result_map)} tiles.")
    return result_map
