# 🪨 Tile Image Extractor

A web application to **automatically extract all tile product images** from PDF catalogues — losslessly, at high speed, unlimited times.

Built by [Somya Bhalani](https://github.com/somyabhalani)

---

## ✨ Features

- ⚡ **Lightning Fast** — Processes large PDF catalogues in seconds using async background tasks
- 🔍 **Smart Tile Detection** — AI-powered filter rejects QR codes, logos, borders, and blank fills — keeping only real tile images
- 🖼️ **Lossless Quality** — All images extracted without any quality loss (JPEG byte-copied, others converted to web-safe PNG)
- 📦 **Download All** — Export every extracted tile as a single ZIP archive in one click
- 📊 **Live Progress Bar** — Real-time extraction progress tracked from backend to frontend
- 🪟 **Masonry Gallery** — Beautiful responsive grid layout with fullscreen lightbox viewer
- 📱 **Mobile Friendly** — Fully responsive design, works on all screen sizes
- 🎨 **Liquid Glass UI** — Premium frosted glass design with marble background

---

## 🚀 Getting Started

### 1. Prerequisites

You will need the following installed on your machine:
- **Python 3.9+** and `pip`

### 2. Installation

Open your terminal or command prompt and run:

```bash
# Clone the repository
git clone https://github.com/somyabhalani/tile-extractor.git

# Navigate into the folder
cd tile-extractor

# Install the required Python libraries
pip install -r requirements.txt
```

### 3. Run Locally

Start the local server using Uvicorn:

```bash
uvicorn app:app --reload
```

### 4. Open in Browser

Once the server is running, open your web browser and go to:
**http://localhost:8000**

---

## 🛠️ Tech Stack

| Layer     | Technology               |
|-----------|--------------------------|
| Backend   | Python, FastAPI, Uvicorn |
| PDF Engine| PyMuPDF (fitz)           |
| Images    | Pillow (PIL)             |
| Frontend  | HTML5, CSS3, Vanilla JS  |
| Design    | Glassmorphism / Liquid Glass |

---

## 📁 Project Structure

```
tile-extractor/
├── app.py               # FastAPI server & API endpoints
├── tile_extractor.py    # Core PDF tile extraction engine
├── requirements.txt     # Python dependencies
├── static/
│   ├── index.html       # Main UI
│   ├── style.css        # Liquid Glass design system
│   ├── script.js        # Frontend logic & progress polling
│   └── marble-bg.png    # Background texture
└── jobs/                # Auto-generated; stores extracted tiles per session
```


## 📖 How It Works

1. **Upload** — Drop a PDF tile catalogue onto the upload area
2. **Extract** — The backend spawns a background task, iterating through every image reference in the PDF
3. **Filter** — Each image passes through a smart `TileFilter` that rejects non-tile content based on size, aspect ratio, edge density, and color statistics
4. **Progress** — The frontend polls `/api/progress/{job_id}` every 500ms and animates a live progress bar
5. **Display** — Extracted tiles are served via `/api/images` and displayed in a beautiful masonry gallery
6. **Download** — Optionally download all tiles as a ZIP archive via `/api/download/{job_id}`

---

## 📞 Contact

**Somya Bhalani**
- GitHub: [@somyabhalani](https://github.com/somyabhalani)
- Phone: +91 8320408204

---

© 2025 Somya Bhalani. All rights reserved.
