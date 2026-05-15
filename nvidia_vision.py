import os
import re
import json
import requests
from dotenv import load_dotenv

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-11b-vision-instruct"


def _scan_single_tile(tile_box: dict, headers: dict) -> dict:
    """Scan a single cropped tile image to extract its specs."""
    crop_b64 = tile_box.get("crop_b64")
    if not crop_b64:
        return {"xref": tile_box["xref"], "text": ""}
        
    prompt = """You are an expert reading tile product catalogs.
I am giving you an image crop containing ONE tile and its surrounding label text.
Extract the product text describing this tile (Product Name, Size, Finish, Code/SKU).
If there is no readable text, return an empty string.

Return ONLY valid JSON in this exact format, nothing else:
{
  "text": "<all extracted product text, separated by |>"
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
        "temperature": 0.10,
        "top_p": 0.90,
        "stream": False
    }

    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        raw_content = result["choices"][0]["message"]["content"]
        
        # Try JSON parsing
        json_match = re.search(r'\{[\s\S]*\}', raw_content)
        if json_match:
            parsed = json.loads(json_match.group())
            text = parsed.get("text", "").strip()
            return {"xref": tile_box["xref"], "text": text}
            
        # Fallback raw extraction (if it just returned text instead of JSON)
        text = raw_content.replace('\n', ' | ').strip()
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
    
    if not NVIDIA_API_KEY:
        raise ValueError("NVIDIA_API_KEY is not set in .env file.")

    if not image_boxes:
        return {}

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
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
