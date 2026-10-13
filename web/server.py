import os
import uuid
import json
import threading
import fitz
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from core.remediator import TaggingService

app = FastAPI(title="Autotagging for Figure - PDF Accessibility Tagging Engine")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "web" / "static"
UPLOADS_DIR = BASE_DIR / "workspace_web"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Task state management with disk persistence
TASKS: Dict[str, Dict[str, Any]] = {}


def save_task_meta(task: dict):
    try:
        task_dir = Path(task["task_dir"])
        meta_file = task_dir / "meta.json"
        serializable = {
            "task_id": task["task_id"],
            "filename": task["filename"],
            "page_count": task["page_count"],
            "status": task["status"],
            "progress": task["progress"],
            "stage_msg": task["stage_msg"],
            "logs": task.get("logs", [])[-30:],
            "stats": task.get("stats", {}),
            "error_message": task.get("error_message")
        }
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to persist task metadata: {e}")


def load_task_meta(task_id: str) -> Optional[dict]:
    task_dir = UPLOADS_DIR / task_id
    meta_file = task_dir / "meta.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["task_dir"] = task_dir
            meta["input_path"] = task_dir / "input.pdf"
            meta["output_path"] = task_dir / f"{Path(meta['filename']).stem}_tagged.pdf"
            meta["json_path"] = task_dir / "tags.json"
            return meta
        except Exception:
            pass
    return None


def get_task(task_id: str) -> Optional[dict]:
    if task_id in TASKS:
        return TASKS[task_id]
    loaded = load_task_meta(task_id)
    if loaded:
        TASKS[task_id] = loaded
        return loaded
    return None


def process_pdf_worker(task_id: str):
    task = get_task(task_id)
    if not task:
        return

    input_pdf = task["input_path"]
    output_pdf = task["output_path"]
    json_path = task["json_path"]

    def progress_cb(current_step, total_steps, message):
        pct = int((current_step / total_steps) * 100)
        task["progress"] = pct
        task["stage_msg"] = message
        task["logs"].append(f"[{current_step}/{total_steps}] {message}")
        save_task_meta(task)

    try:
        task["status"] = "processing"
        task["logs"].append("Starting automated PDF accessibility remediation...")
        save_task_meta(task)

        result = TaggingService.remediate_pdf(
            input_pdf=str(input_pdf),
            output_pdf=str(output_pdf),
            progress_callback=progress_cb
        )

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result["doc_json"], f, indent=2, ensure_ascii=False)

        task["status"] = "completed"
        task["progress"] = 100
        task["stage_msg"] = "Remediation complete! PDF/UA tagged file ready."
        task["stats"] = result.get("stats", {})
        task["doc_json"] = result.get("doc_json", {})
        task["logs"].append("Tagged PDF successfully exported with StructTreeRoot and ParentTree.")
        save_task_meta(task)
    except Exception as e:
        task["status"] = "error"
        task["error_message"] = str(e)
        task["logs"].append(f"Error during tagging: {e}")
        save_task_meta(task)


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>TagPilot Web</h1><p>Static index.html not found</p>"


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    task_id = str(uuid.uuid4())[:8]
    task_dir = UPLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    input_path = task_dir / "input.pdf"
    output_path = task_dir / f"{Path(file.filename).stem}_tagged.pdf"
    json_path = task_dir / "tags.json"

    # Stream file to disk in chunks to handle arbitrary file sizes safely
    chunk_size = 1024 * 1024  # 1MB
    total_bytes = 0
    with open(input_path, "wb") as f:
        while chunk := await file.read(chunk_size):
            f.write(chunk)
            total_bytes += len(chunk)

    # Inspect page count
    try:
        doc = fitz.open(str(input_path))
        page_count = len(doc)
        doc.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid or corrupted PDF file: {e}")

    task = {
        "task_id": task_id,
        "task_dir": task_dir,
        "filename": file.filename,
        "input_path": input_path,
        "output_path": output_path,
        "json_path": json_path,
        "page_count": page_count,
        "status": "ready",
        "progress": 0,
        "stage_msg": "Document uploaded and validated.",
        "logs": [f"Uploaded '{file.filename}' ({page_count} page(s), {round(total_bytes/1024, 1)} KB)."],
        "stats": {},
        "doc_json": {}
    }

    TASKS[task_id] = task
    save_task_meta(task)

    return {
        "task_id": task_id,
        "filename": file.filename,
        "page_count": page_count,
        "size_kb": round(total_bytes / 1024, 1)
    }


@app.post("/api/process/{task_id}")
async def trigger_process(task_id: str, background_tasks: BackgroundTasks):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    if task["status"] == "processing":
        return {"message": "Already processing."}

    background_tasks.add_task(process_pdf_worker, task_id)
    return {"message": "Processing started", "task_id": task_id}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    return {
        "status": task["status"],
        "progress": task["progress"],
        "stage_msg": task["stage_msg"],
        "logs": task.get("logs", [])[-15:],
        "stats": task.get("stats", {}),
        "error_message": task.get("error_message")
    }


# Backward-compatible alias for /api/jobs/{task_id}
@app.get("/api/jobs/{task_id}")
async def get_job_status(task_id: str):
    return await get_status(task_id)


@app.get("/api/results/{task_id}")
async def get_results(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    # Load doc_json from disk if not in memory
    doc_json = task.get("doc_json")
    if not doc_json and task.get("json_path") and Path(task["json_path"]).exists():
        try:
            with open(task["json_path"], "r", encoding="utf-8") as f:
                doc_json = json.load(f)
                task["doc_json"] = doc_json
        except Exception:
            pass

    return {
        "task_id": task_id,
        "filename": task["filename"],
        "page_count": task["page_count"],
        "stats": task.get("stats", {}),
        "doc_json": doc_json or {}
    }


@app.get("/api/page_preview/{task_id}/{page_num}")
async def get_page_preview(task_id: str, page_num: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    input_path = str(task["input_path"])
    try:
        doc = fitz.open(input_path)
        if page_num < 1 or page_num > len(doc):
            doc.close()
            raise HTTPException(status_code=400, detail="Page number out of bounds.")

        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=130)
        img_bytes = pix.tobytes("png")
        doc.close()
        return Response(content=img_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download/{task_id}")
async def download_tagged_pdf(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    output_path = Path(task["output_path"])
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Tagged PDF file not found.")

    out_name = f"{Path(task['filename']).stem}_tagged_accessible.pdf"
    return FileResponse(
        path=str(output_path),
        filename=out_name,
        media_type="application/pdf"
    )


@app.get("/api/download_json/{task_id}")
async def download_json(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    json_path = Path(task["json_path"])
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="JSON metadata not found.")

    out_name = f"{Path(task['filename']).stem}_tags.json"
    return FileResponse(
        path=str(json_path),
        filename=out_name,
        media_type="application/json"
    )
