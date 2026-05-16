import os
import csv
import json
import shutil
import uuid
import sys
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

from tile_extractor import TileCatalogueExtractor
from nvidia_vision import scan_full_page_products

app = FastAPI(title="Tile Extractor API")

JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

PROGRESS_STATE = {}

def run_extraction_task(job_id: str, pdf_path: str, output_dir: str):
    print(f"INFO: Starting extraction for job {job_id}")
    PROGRESS_STATE[job_id] = {"status": "processing", "current": 0, "total": 0, "percentage": 0}
    try:
        extractor = TileCatalogueExtractor(
            pdf_path=pdf_path,
            output_dir=output_dir,
            verbose=True
        )
        def progress_cb(current, total):
            PROGRESS_STATE[job_id]["percentage"] = current
            PROGRESS_STATE[job_id]["total"] = total
                
        extractor.extract_images(progress_callback=progress_cb)
        PROGRESS_STATE[job_id]["status"] = "completed"
        PROGRESS_STATE[job_id]["percentage"] = 100
        print(f"INFO: Completed extraction for job {job_id}")
    except Exception as e:
        print(f"ERROR: Job {job_id} failed: {e}")
        PROGRESS_STATE[job_id]["status"] = "error"
        PROGRESS_STATE[job_id]["error"] = str(e)

@app.post("/api/upload")
async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    
    pdf_path = job_dir / file.filename
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
        
    output_dir = job_dir / "output"
    output_dir.mkdir(exist_ok=True)
    
    background_tasks.add_task(run_extraction_task, job_id, str(pdf_path), str(output_dir))
        
    return {"job_id": job_id, "message": "Extraction started."}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    if job_id not in PROGRESS_STATE:
        return {"status": "unknown"}
    return PROGRESS_STATE[job_id]

@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    job_dir = JOBS_DIR / job_id
    csv_path = job_dir / "output" / "tiles.csv"

    if not csv_path.exists():
        return JSONResponse(status_code=404, content={"error": "Results not ready yet."})

    # Load images from CSV
    images_by_page = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pno = row["page"]
            images_by_page.setdefault(pno, []).append(row)

    # Build page-grouped response
    pages = []
    for pno in sorted(images_by_page.keys(), key=lambda x: int(x)):
        images = images_by_page.get(pno, [])
        pages.append({
            "page": int(pno),
            "images": images
        })

    return {"job_id": job_id, "pages": pages}

@app.get("/api/images/{job_id}/{filename}")
async def get_image(job_id: str, filename: str):
    image_path = (JOBS_DIR / job_id / "output" / filename).absolute()
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)

@app.get("/api/download/{job_id}")
async def download_all(job_id: str):
    job_dir = JOBS_DIR / job_id
    output_dir = job_dir / "output"
    zip_filename = f"tiles_{job_id}"
    zip_path = shutil.make_archive(str(job_dir / zip_filename), 'zip', str(output_dir))
    return FileResponse(zip_path, filename=f"extracted_tiles.zip")

@app.get("/api/download-page/{job_id}/{page_num}")
async def download_page_assets(job_id: str, page_num: int):
    """Download a ZIP containing images for a specific page and a data.json with their text info."""
    import zipfile
    import json
    
    job_dir = JOBS_DIR / job_id
    output_dir = job_dir / "output"
    csv_path = output_dir / "tiles.csv"
    
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Job results not found.")

    # Read CSV to find images for this page and their text
    product_list = ""
    tiles = []
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row.get("page", 0)) == page_num:
                fname = row.get("filename", "")
                if fname and (output_dir / fname).exists():
                    tiles.append({
                        "filename": fname,
                        "width": row.get("width"),
                        "height": row.get("height")
                    })
                    # Capture the product info from the first matching row
                    if not product_list:
                        raw_csv = row.get("product_text", "")
                        try:
                            data = json.loads(raw_csv)
                            # Prefer structured products list if it has entries
                            if isinstance(data.get("products"), list) and data["products"]:
                                product_list = data["products"]
                            # Otherwise use raw_text or display as fallback
                            elif data.get("raw_text"):
                                product_list = data["raw_text"]
                            elif data.get("display"):
                                product_list = data["display"]
                            else:
                                product_list = []
                        except:
                            product_list = raw_csv
                    
    if not tiles:
        raise HTTPException(status_code=404, detail="No images found for this page.")

    # Build the JSON exactly like the model output
    if isinstance(product_list, list) and product_list:
        page_data = {
            "page_number": page_num,
            "products": product_list,
            "tiles": tiles
        }
    elif isinstance(product_list, str) and product_list.strip():
        # Fallback: raw text — split into clean lines so JSON is readable
        clean_lines = [line.strip() for line in product_list.replace('\\n', '\n').replace('\\t', ' ').split('\n') if line.strip()]
        page_data = {
            "page_number": page_num,
            "products": [],
            "extracted_text": clean_lines,
            "tiles": tiles
        }
    else:
        page_data = {
            "page_number": page_num,
            "products": [],
            "tiles": tiles
        }

    # Create a temporary ZIP file specifically for this page
    zip_filename = f"page_{page_num}_{job_id}.zip"
    zip_path = job_dir / zip_filename

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Add images
        for tile in tiles:
            file_path = output_dir / tile["filename"]
            zipf.write(file_path, arcname=tile["filename"])

        # Add page_data.json — exact model output format
        json_data = json.dumps(page_data, indent=4, ensure_ascii=False)
        zipf.writestr("page_data.json", json_data)

    return FileResponse(zip_path, filename=f"page_{page_num}_tiles.zip")

@app.post("/api/scan-text/{job_id}/{page_num}")
async def scan_text_for_page(job_id: str, page_num: int):
    """Scan the entire page as one image and extract all product details using 90B model."""
    job_dir = JOBS_DIR / job_id
    csv_path = job_dir / "output" / "tiles.csv"

    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Job results not found.")

    pdf_files = list(job_dir.glob("*.pdf"))
    if not pdf_files:
        raise HTTPException(status_code=404, detail="PDF not found.")
    pdf_path = str(pdf_files[0])

    try:
        extractor = TileCatalogueExtractor(pdf_path=pdf_path, output_dir=str(job_dir / "output"))
        
        # Step 1: Render FULL page at high-res (3x zoom)
        image_b64 = extractor.get_full_page_image(page_num)

        # Step 2: Use AI to extract all products from the page as a structured list
        result_dict = scan_full_page_products(image_b64)
        products = result_dict.get("products", [])
        raw_text = result_dict.get("raw_text", "")

        # Step 3: Build the display text
        display_text = ""

        if raw_text and not products:
            # Fallback mode: parse the raw text to extract products and coordinates
            display_text = raw_text
            
            # Use regex to split into blocks and find coordinates
            blocks = re.split(r'(?:\n|^)\d+\.\s+\*\*', raw_text)
            for block in blocks:
                if not block.strip(): continue
                
                name_match = re.search(r'^(.*?)\*\*', block)
                name = name_match.group(1).strip() if name_match else "N/A"
                
                coord_match = re.search(r'Coordinates:.*?\[(\d+),\s*(\d+)\]', block)
                if coord_match:
                    try:
                        cx = int(coord_match.group(1))
                        cy = int(coord_match.group(2))
                        products.append({
                            "name": name,
                            "coordinates": [cx, cy],
                            "display": block.strip()
                        })
                    except: pass
        else:
            # Structured mode: build pretty display from product list
            for i, p in enumerate(products, 1):
                name = p.get('name') or "N/A"
                collection = p.get('collection') or "N/A"
                text_block = f"**{name}**\n* Collection: {collection}\n"
                text_block += f"* Size: {p.get('size') or 'N/A'}\n"
                text_block += f"* Finish: {p.get('surface') or 'N/A'}\n"
                text_block += f"* Faces: {p.get('faces') or 'N/A'}\n"
                text_block += f"* Thickness: {p.get('thickness') or 'N/A'}\n"
                text_block += f"* Position: {p.get('position') or 'N/A'}\n"
                text_block += f"* Description: {p.get('image_description') or 'N/A'}"
                
                p["display"] = text_block # Store isolated text for later matching
                display_text += f"**Product {i}: {name}**\n{text_block}\n\n"

        if not display_text.strip():
            display_text = "No products detected on this page."

        # Step 4: Update CSV and perform Coordinate Matching
        rows = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)

        # Coordinate Matching Engine
        tile_matches = {} # filename -> matched display text
        
        # Only attempt coordinate matching if we have structured products with coordinates
        can_coordinate_match = bool(products and all(isinstance(p.get("coordinates"), list) and len(p["coordinates"]) == 2 for p in products))
        
        for row in rows:
            if int(row.get("page", 0)) == page_num:
                matched_text = display_text # fallback to full text
                
                if can_coordinate_match:
                    try:
                        # Get exact tile physical center from CSV
                        cx = float(row.get("center_x", 0))
                        cy = float(row.get("center_y", 0))
                        
                        best_dist = float('inf')
                        best_product = None
                        best_idx = 0
                        
                        # Find closest AI product coordinate
                        for i, p in enumerate(products, 1):
                            ai_coords = p["coordinates"]
                            dist = ((cx - ai_coords[0]) ** 2 + (cy - ai_coords[1]) ** 2) ** 0.5
                            if dist < best_dist:
                                best_dist = dist
                                best_product = p
                                best_idx = i
                                
                        if best_product:
                            matched_text = best_product.get("display") or display_text
                    except Exception as e:
                        print(f"WARN: Coordinate match failed for {row['filename']}: {e}")
                
                # We save a specific JSON for this row, carrying its isolated matched text
                row["product_text"] = json.dumps({
                    "display": matched_text,
                    "products": products, # keep full array for download
                    "raw_text": raw_text
                })
                tile_matches[row["filename"]] = matched_text

        if "product_text" not in fieldnames:
            fieldnames = list(fieldnames) + ["product_text"]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        return {
            "page": page_num,
            "status": "success",
            "full_text": display_text, # Return pretty text for the page modal
            "tile_matches": tile_matches # Precise coordinate-matched text per tile
        }

    except Exception as e:
        print(f"ERROR: scan-text failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
