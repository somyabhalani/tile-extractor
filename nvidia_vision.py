import os
import re
import json
import requests
from dotenv import load_dotenv

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-11b-vision-instruct"


def _build_prompt_visual(image_boxes: list) -> str:
    """
    Build a prompt that tells the model to visually read ALL text on the page
    itself — no pre-extracted text needed. Works even for rasterized PDFs.
    """
    img_list = "\n".join([
        f"  - Tile #{i+1}: xref={b['xref']}, pixel region [x0={b['x0']}, y0={b['y0']}, x1={b['x1']}, y1={b['y1']}]"
        for i, b in enumerate(image_boxes)
    ])

    prompt = f"""You are an expert at reading tile and flooring product catalogues.

I am giving you a full-page image from a PDF tile catalogue. The page contains tile product photos and product text labels (names, sizes, finishes, codes, etc.).

TILE IMAGES I have detected on this page (with their pixel bounding boxes):
{img_list if img_list else "  (none found)"}

YOUR TASK:
1. Look carefully at the ENTIRE page image — read ALL visible text on the page.
2. For EACH tile listed above, extract the EXACT text block(s) that correspond to it. This includes:
   - Product / collection name
   - Tile size / dimensions (e.g. 600x1200, 800x800 mm)
   - Surface finish (e.g. Polished, Matt, Satin, Glazed, Natural)
   - Texture or look (e.g. Marble, Stone, Wood, Concrete)
   - Product code / SKU
   - Any technical spec, shade, or grade visible near that tile
3. CRITICAL: Only match text to a tile if it clearly describes THAT specific tile based on visual layout (e.g., text is directly underneath, inside the same grid cell, or explicitly labels the tile). Do NOT mix up text from adjacent tiles.
4. Text that is a general page header, brand logo, or page number should be ignored.
5. If a tile has no readable text specifically belonging to it, return an empty string for that tile.

IMPORTANT: Even if the text appears to be part of the background image (not selectable), READ IT VISUALLY from the image I am providing.

Return ONLY valid JSON in this exact format, nothing else — no markdown, no explanation:
{{
  "associations": [
    {{
      "xref": <xref integer>,
      "text": "<all product text for this SPECIFIC tile ONLY, use | to separate multiple items. Do NOT include text from other tiles.>"
    }}
  ]
}}"""
    return prompt


def associate_text_to_tiles(image_b64: str, image_boxes: list, text_blocks: list) -> dict:
    """
    Call NVIDIA NIM vision model to visually read text from the full page image
    and associate it with each tile. Does NOT rely on pre-extracted text blocks.
    Returns a dict: { xref: "matched text" }
    """
    if not NVIDIA_API_KEY:
        raise ValueError("NVIDIA_API_KEY is not set in .env file.")

    if not image_boxes:
        return {}

    # Always call the vision model — let it visually read text from the image
    prompt = _build_prompt_visual(image_boxes)

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
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.10,
        "top_p": 0.90,
        "stream": False
    }

    try:
        print(f"INFO: Calling NVIDIA API for {len(image_boxes)} tiles...")
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        result = response.json()
        raw_content = result["choices"][0]["message"]["content"]
        print(f"INFO: NVIDIA raw response: {raw_content[:600]}")

        # --- Try JSON parse first ---
        json_match = re.search(r'\{[\s\S]*\}', raw_content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                associations = parsed.get("associations", [])
                result_map = {}
                for item in associations:
                    result_map[int(item["xref"])] = item.get("text", "").strip()
                if result_map:
                    print(f"INFO: Got {len(result_map)} associations via JSON")
                    return result_map
            except json.JSONDecodeError:
                pass

        # --- Fallback: parse the model's markdown/text response ---
        # Model often returns "Tile 1: Name\n* Size: ...\n* Finish: ..."
        # We map Tile #N back to the xref by index (same order we sent them)
        print("INFO: JSON parse failed, trying markdown fallback parser...")
        result_map = {}

        # Split by tile sections: "Tile 1", "Tile 2" etc.
        tile_sections = re.split(r'\*{0,2}Tile\s*#?\s*(\d+)\s*[:\-]?\*{0,2}', raw_content, flags=re.IGNORECASE)

        # tile_sections will be: [pre-text, "1", content1, "2", content2, ...]
        i = 1
        while i < len(tile_sections) - 1:
            tile_num_str = tile_sections[i].strip()
            tile_content = tile_sections[i + 1].strip() if i + 1 < len(tile_sections) else ""

            try:
                tile_num = int(tile_num_str)  # 1-indexed
                xref_index = tile_num - 1
                if 0 <= xref_index < len(image_boxes):
                    xref = image_boxes[xref_index]["xref"]

                    # Extract meaningful lines (strip markdown bullets and bold)
                    lines = []
                    for line in tile_content.split('\n'):
                        line = line.strip()
                        line = re.sub(r'^\*+\s*', '', line)          # remove leading *
                        line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)  # remove **bold**
                        line = re.sub(r'\*([^*]+)\*', r'\1', line)      # remove *italic*
                        # Only keep lines that have actual content (not "Not visible" etc.)
                        if line and ':' in line:
                            key, _, val = line.partition(':')
                            val = val.strip()
                            if val and val.lower() not in ('not visible', 'n/a', 'not available', 'unknown', ''):
                                lines.append(f"{key.strip()}: {val}")
                        elif line and len(line) > 2 and 'not visible' not in line.lower():
                            lines.append(line)

                    # Take first 5 meaningful lines max
                    text = " | ".join(lines[:5])
                    if text:
                        result_map[xref] = text
            except (ValueError, IndexError):
                pass
            i += 2

        print(f"INFO: Got {len(result_map)} associations via markdown fallback")
        return result_map

    except requests.exceptions.Timeout:
        raise RuntimeError("NVIDIA API request timed out. Please try again.")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"NVIDIA API error: {e.response.status_code} - {e.response.text}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse model response as JSON: {e}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error calling NVIDIA API: {e}")
