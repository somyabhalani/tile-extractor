import os
import csv
import json
import re
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
SCAN_STATE = {} # { job_id: { page_num: { status: "pending", result: None } } }

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

    # Build the JSON exactly like the model output — Structured and Clean
    page_data = {
        "page_number": page_num,
        "job_id": job_id,
        "products": product_list, # This is now a structured list of dicts
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

        # Add page_data.json — Pretty-printed with no extra escaping
        json_data = json.dumps(page_data, indent=4, ensure_ascii=False)
        zipf.writestr("page_data.json", json_data)

    return FileResponse(zip_path, filename=f"page_{page_num}_tiles.zip")

def run_scan_task(job_id: str, page_num: int, pdf_path: str, output_dir: str, csv_path: str):
    """Background task for scanning text."""
    try:
        SCAN_STATE.setdefault(job_id, {})[page_num] = {"status": "scanning"}
        
        extractor = TileCatalogueExtractor(pdf_path=pdf_path, output_dir=output_dir)
        
        # Step 1: Render FULL page at high-res
        image_b64 = extractor.get_full_page_image(page_num)

        # Step 2: AI Extraction
        result_dict = scan_full_page_products(image_b64)
        products = result_dict.get("products", [])
        raw_text = result_dict.get("raw_text", "")

        # Step 3: Build display text and ensure structured objects
        display_text = ""
        structured_products = []

        if raw_text and not products:
            display_text = raw_text
            blocks = re.split(r'(?:\n|^)(?:\d+\.|\*)\s*\*\*', raw_text)
            for block in blocks:
                if not block.strip() or len(block) < 20: continue
                name_match = re.search(r'^(.*?)\*\*', block)
                name = name_match.group(1).strip() if name_match else "N/A"
                
                # Clean up the block text for display
                clean_display = block.strip().replace("\t", " ").replace("\\n", "\n")
                
                prod_obj = {
                    "name": name,
                    "display": clean_display
                }
                products.append(prod_obj)
                structured_products.append(prod_obj)
        else:
            for i, p in enumerate(products, 1):
                name = p.get('name') or "N/A"
                
                # Ensure each field is a clean string, not null
                p["name"] = name
                p["collection"] = p.get("collection") or "N/A"
                p["size"] = p.get("size") or "N/A"
                p["surface"] = p.get("surface") or p.get("finish") or "N/A"
                p["faces"] = p.get("faces") or "N/A"
                p["thickness"] = p.get("thickness") or "N/A"
                p["position"] = p.get("position") or "N/A"
                p["description"] = p.get("image_description") or "N/A"

                text_block = f"**{name}**\n* Size: {p['size']}\n"
                text_block += f"* Size: {p['size']}\n"
                text_block += f"* Finish: {p['surface']}\n"
                text_block += f"* Faces: {p['faces']}\n"
                text_block += f"* Thickness: {p['thickness']}\n"
                text_block += f"* Position: {p['position']}\n"
                text_block += f"* Description: {p['description']}"
                
                p["display"] = text_block
                
                # FINAL FILTER: Only add if it has a name and at least some technical spec (like size)
                if name != "N/A" and p["size"] != "N/A":
                    display_text += f"**Product {i}: {name}**\n{text_block}\n\n"
                    structured_products.append(p)
                else:
                    print(f"INFO: Filtered out non-product header: {name}")

        if not display_text.strip():
            display_text = "No products detected on this page."

        # Step 4: Update CSV
        rows = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        if "product_text" not in fieldnames:
            fieldnames.append("product_text")

        tile_matches = {}
        page_tiles = [row for row in rows if int(row.get("page", 0)) == page_num]
        primary_tiles = [t for t in page_tiles if float(t.get("width", 0)) > 350 or float(t.get("height", 0)) > 350]
        if not primary_tiles: primary_tiles = page_tiles 

        for row in page_tiles:
            matched_text = display_text
            if products:
                try:
                    current_tile_idx = page_tiles.index(row)
                    product_group_idx = -1
                    for pt in primary_tiles:
                        if current_tile_idx >= page_tiles.index(pt):
                            product_group_idx += 1
                        else: break
                    if product_group_idx >= 0 and product_group_idx < len(products):
                        matched_text = products[product_group_idx].get("display", display_text)
                    elif len(products) == 1:
                        matched_text = products[0].get("display", display_text)
                except: pass
            
            row["product_text"] = json.dumps({"display": matched_text, "products": products, "raw_text": raw_text})
            tile_matches[row["filename"]] = matched_text

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        SCAN_STATE[job_id][page_num] = {
            "status": "completed",
            "result": {
                "page": page_num,
                "full_text": display_text,
                "tile_matches": tile_matches
            }
        }
    except Exception as e:
        print(f"ERROR: Scan job {job_id} p{page_num} failed: {e}")
        SCAN_STATE[job_id][page_num] = {"status": "error", "error": str(e)}

@app.post("/api/scan-text/{job_id}/{page_num}")
async def scan_text_for_page(job_id: str, page_num: int, background_tasks: BackgroundTasks):
    """Start background scan."""
    job_dir = JOBS_DIR / job_id
    csv_path = job_dir / "output" / "tiles.csv"
    if not csv_path.exists(): raise HTTPException(status_code=404, detail="Job results not found.")
    
    pdf_files = list(job_dir.glob("*.pdf"))
    if not pdf_files: raise HTTPException(status_code=404, detail="PDF not found.")
    
    background_tasks.add_task(run_scan_task, job_id, page_num, str(pdf_files[0]), str(job_dir / "output"), str(csv_path))
    return {"status": "started", "message": "Scan background task initiated."}

@app.get("/api/scan-status/{job_id}/{page_num}")
async def get_scan_status(job_id: str, page_num: int):
    """Poll for scan results."""
    job_scans = SCAN_STATE.get(job_id, {})
    return job_scans.get(page_num, {"status": "not_found"})


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
