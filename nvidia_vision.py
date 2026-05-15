import os
import requests
import json
import re

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.2-90b-vision-instruct"

def scan_full_page_products(image_b64: str) -> str:
    """
    Sends a high-res full page image to NVIDIA 90B to extract all products.
    Returns a formatted string of product info.
    """
    if not NVIDIA_API_KEY:
        return "Error: NVIDIA_API_KEY not found."

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json"
    }

    prompt = """You are a professional tile catalogue data extractor.
Look at this entire page and identify EVERY tile product shown.

For each product, extract:
1. Product Name (SKU/Code)
2. Size (e.g., 600x600mm)
3. Finish/Surface (e.g., Polished, Matt, Rustic)
4. Number of Faces (if mentioned)
5. Thickness (e.g., 9mm, 12mm)

RULES:
- If multiple images represent the SAME product (e.g., different faces), group them as ONE entry.
- Return the info in a clean, human-readable format.
- Use bullet points for each product.
- DO NOT use conversational fluff like "The image shows". Start directly with the products.
"""

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": f'{prompt} <img src="data:image/jpeg;base64,{image_b64}" />'
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.1
    }

    print(f"INFO: Sending full page (90B Vision) to NVIDIA API...")
    try:
        response = requests.post(INVOKE_URL, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        result = response.json()
        return result['choices'][0]['message']['content']
    except Exception as e:
        return f"Error scanning page: {str(e)}"
