#!/usr/bin/env python3
import os
import csv
import io
import hashlib
import base64
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
            page_width = page.rect.width
            page_height = page.rect.height
            
            # Get images with bounding boxes
            images_info = page.get_image_info(xrefs=True)
            
            for img_info in images_info:
                xref = img_info["xref"]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                
                # Calculate center point in percentages (0-100)
                bbox = img_info["bbox"] # (x0, y0, x1, y1)
                center_x = ((bbox[0] + bbox[2]) / 2 / page_width) * 100
                center_y = ((bbox[1] + bbox[3]) / 2 / page_height) * 100
                
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    ext = base_image["ext"]
                    w = base_image["width"]
                    h = base_image["height"]
                    
                    if w < 100 or h < 100:
                        self.stats["skipped_small"] += 1
                        continue
                        
                    h_hash = hashlib.md5(image_bytes).hexdigest()
                    if h_hash in self.seen_hashes:
                        self.stats["skipped_dup"] += 1
                        continue
                    self.seen_hashes.add(h_hash)

                    filename = f"tile_p{pno+1:04d}_{w}x{h}_{xref}.{ext}"
                    (self.output_dir / filename).write_bytes(image_bytes)
                    
                    self._csv_rows.append({
                        "page": pno + 1,
                        "filename": filename,
                        "width": w,
                        "height": h,
                        "size": len(image_bytes),
                        "format": ext.upper(),
                        "center_x": round(center_x, 2),
                        "center_y": round(center_y, 2),
                        "product_text": ""
                    })
                    self.stats["tiles_saved"] += 1
                    
                except Exception as e:
                    continue
            
            if progress_callback:
                progress_callback(int((pno + 1) / total_pages * 100), 100)
        
        self._write_csv()
        doc.close()
        return self.stats["tiles_saved"]

    def _write_csv(self):
        csv_path = self.output_dir / "tiles.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["page", "filename", "width", "height", "size", "format", "center_x", "center_y", "product_text"])
            writer.writeheader()
            writer.writerows(self._csv_rows)

    def get_full_page_image(self, page_num: int):
        """Render the entire page as a high-resolution base64 string, optimized for NVIDIA."""
        doc = fitz.open(self.pdf_path)
        page = doc[page_num - 1]
        
        # 1. Render at high resolution (2x zoom)
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        
        # 2. Convert to PIL for smart resizing
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        
        # 3. Cap the maximum dimension to 2048px (NVIDIA's fast-processing limit)
        max_dim = 2048
        if img.width > max_dim or img.height > max_dim:
            if img.width > img.height:
                new_w = max_dim
                new_h = int(img.height * (max_dim / img.width))
            else:
                new_h = max_dim
                new_w = int(img.width * (max_dim / img.height))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            print(f"DEBUG: Resized page from {pix.width}x{pix.height} to {new_w}x{new_h}")

        # 4. Save as high-quality JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        img_bytes = buffer.getvalue()
        
        print(f"DEBUG: Optimized Page Image size: {len(img_bytes)/1024:.1f} KB")
        doc.close()
        return base64.b64encode(img_bytes).decode("utf-8")
