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
    page_data = {}
    images_to_zip = []
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row.get("page", 0)) == page_num:
                fname = row.get("filename", "")
                if fname and (output_dir / fname).exists():
                    images_to_zip.append(fname)
                    page_data[fname] = row.get("product_text", "")
                    
    if not images_to_zip:
        raise HTTPException(status_code=404, detail="No images found for this page.")

    # Create a temporary ZIP file specifically for this page
    zip_filename = f"page_{page_num}_{job_id}.zip"
    zip_path = job_dir / zip_filename
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Add images
        for fname in images_to_zip:
            file_path = output_dir / fname
            zipf.write(file_path, arcname=fname)
            
        # Add data.json
        json_data = json.dumps(page_data, indent=4)
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

        # Step 2: Use 90B to extract all products from the page
        full_text_results = scan_full_page_products(image_b64)

        # Step 3: Update CSV (Assign this info to all tiles on this page for now, as context)
        rows = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)

        if "product_text" not in fieldnames:
            fieldnames = list(fieldnames) + ["product_text"]

        for row in rows:
            if int(row.get("page", 0)) == page_num:
                row["product_text"] = full_text_results

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        return {
            "page": page_num,
            "status": "success",
            "full_text": full_text_results
        }

    except Exception as e:
        print(f"ERROR: scan-text failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
