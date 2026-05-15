#!/usr/bin/env python3
import os
import csv
import io
import hashlib
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF

class TileCatalogueExtractor:
    def __init__(self, pdf_path: str, output_dir: str, verbose: bool = False):
        self.pdf_path = pdf_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.seen_hashes = set()
        self.stats = {"pages": 0, "tiles_saved": 0, "skipped_dup": 0, "skipped_small": 0}
        self._csv_rows = []

    def extract_images(self, progress_callback=None):
        """Extract all images from the PDF while avoiding duplicates and small icons."""
        doc = fitz.open(self.pdf_path)
        total_pages = len(doc)
        self.stats["pages"] = total_pages
        
        seen_xrefs = set()

        for pno in range(total_pages):
            page = doc[pno]
            # Use full=True to get the xref directly in the first element of each tuple
            images = page.get_images(full=True)
            
            for img_info in images:
                xref = img_info[0]
                
                # If we've already processed this image object in this PDF, skip it
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                
                try:
                    # Extract the image using its unique reference ID
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    ext = base_image["ext"]
                    w = base_image["width"]
                    h = base_image["height"]
                    
                    # Filter out tiny images (icons, logos, etc.)
                    if w < 100 or h < 100:
                        self.stats["skipped_small"] += 1
                        continue
                        
                    # Use MD5 to check for identical images across different xrefs (common in PDFs)
                    h_hash = hashlib.md5(image_bytes).hexdigest()
                    if h_hash in self.seen_hashes:
                        self.stats["skipped_dup"] += 1
                        continue
                    self.seen_hashes.add(h_hash)

                    # Save the image
                    filename = f"tile_p{pno+1:04d}_{w}x{h}_{xref}.{ext}"
                    (self.output_dir / filename).write_bytes(image_bytes)
                    
                    self._csv_rows.append({
                        "page": pno + 1,
                        "filename": filename,
                        "width": w,
                        "height": h,
                        "size": len(image_bytes),
                        "format": ext.upper()
                    })
                    self.stats["tiles_saved"] += 1
                    
                except Exception as e:
                    if self.verbose:
                        print(f"Error extracting image {xref} on page {pno+1}: {e}")
                    continue
            
            if progress_callback:
                # Update progress (0-100)
                progress_callback(int((pno + 1) / total_pages * 100), 100)
        
        # Write CSV report
        self._write_csv()
        doc.close()
        return self.stats["tiles_saved"]

    def _write_csv(self):
        csv_path = self.output_dir / "tiles.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["page", "filename", "width", "height", "size", "format"])
            writer.writeheader()
            writer.writerows(self._csv_rows)

    def scan_metadata(self, progress_callback=None):
        """Stub for backward compatibility - metadata scanning is removed."""
        if progress_callback:
            progress_callback(100, 100)
        return {}

    def match_text_locally(self, page_num: int):
        """
        Extract text for tiles using Local OCR (EasyOCR).
        This works on ANY PDF (Scanned or Digital) and runs 100% locally.
        """
        import easyocr
        import numpy as np
        
        # Initialize reader (singleton-style to save memory/time)
        if not hasattr(self, '_ocr_reader'):
            print("INFO: Initializing Local OCR Engine (EasyOCR)...")
            self._ocr_reader = easyocr.Reader(['en'], gpu=False) # Use CPU to ensure it works everywhere
        
        doc = fitz.open(self.pdf_path)
        page = doc[page_num - 1]
        
        # Get all image xrefs and their rectangles on the page
        images = page.get_images(full=True)
        results = {}

        for img_info in images:
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            
            # Create a crop around the image to find its text label
            rect = rects[0]
            padding = 150 # Large padding to ensure we grab the text
            padded_rect = fitz.Rect(
                max(0, rect.x0 - padding),
                max(0, rect.y0 - padding),
                min(page.rect.width, rect.x1 + padding),
                min(page.rect.height, rect.y1 + padding)
            )
            
            # Render the crop at 2x resolution for better OCR
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=padded_rect)
            img_bytes = pix.tobytes("png")
            
            # Run OCR on the crop
            try:
                ocr_results = self._ocr_reader.readtext(img_bytes, detail=0)
                # Filter out garbage or very short strings
                cleaned_text = " | ".join([t.strip() for t in ocr_results if len(t.strip()) > 2])
                results[xref] = cleaned_text
            except Exception as e:
                print(f"ERROR: OCR failed for xref {xref}: {e}")
                results[xref] = ""

        doc.close()
        return results

    def extract_page_layout(self, page_num: int):
        """
        For a given page (1-indexed), extract:
        - Image bounding boxes with their xrefs
        - Base64 cropped images (the tile + 120pt padding)
        """
        import base64
        doc = fitz.open(self.pdf_path)
        page = doc[page_num - 1]
        
        mat = fitz.Matrix(2.0, 2.0)
        page_rect = page.rect

        image_boxes = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            if rects:
                rect = rects[0]
                padding = 120
                padded_rect = fitz.Rect(
                    max(0, rect.x0 - padding),
                    max(0, rect.y0 - padding),
                    min(page_rect.width, rect.x1 + padding),
                    min(page_rect.height, rect.y1 + padding)
                )
                
                pix = page.get_pixmap(matrix=mat, clip=padded_rect)
                img_bytes = pix.tobytes("png")
                crop_b64 = base64.b64encode(img_bytes).decode("utf-8")
                
                image_boxes.append({
                    "xref": xref,
                    "crop_b64": crop_b64,
                    "x0": round(rect.x0 * 2),
                    "y0": round(rect.y0 * 2),
                    "x1": round(rect.x1 * 2),
                    "y1": round(rect.y1 * 2),
                })

        doc.close()
        return {
            "page": page_num,
            "image_b64": "",
            "image_boxes": image_boxes,
            "text_blocks": [],
        }
