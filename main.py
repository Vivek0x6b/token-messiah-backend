from __future__ import annotations
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from converter import extract_markdown, get_stats
from typing import List
import io
import os
import zipfile
import httpx

app = FastAPI(title="Token Messiah API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
GROQ_API_URL  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "llama-3.1-8b-instant"


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "message": "Token Messiah API is running"}


# ─── Single PDF convert ───────────────────────────────────────────────────────

@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(413, "File too large. Max 50MB.")

    try:
        markdown, page_count = extract_markdown(io.BytesIO(contents))
        stats = get_stats(markdown, page_count)
        return JSONResponse({"markdown": markdown, "stats": stats})
    except Exception as e:
        raise HTTPException(500, f"Conversion failed: {str(e)}")


# ─── Batch convert → zip ──────────────────────────────────────────────────────

@app.post("/batch")
async def batch(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No files provided.")
    if len(files) > 10:
        raise HTTPException(400, "Max 10 files per batch.")

    zip_buffer = io.BytesIO()
    results = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if not f.filename.lower().endswith(".pdf"):
                results.append({"file": f.filename, "error": "Not a PDF"})
                continue

            contents = await f.read()
            if len(contents) > MAX_FILE_SIZE:
                results.append({"file": f.filename, "error": "Too large (max 50MB)"})
                continue

            try:
                markdown, page_count = extract_markdown(io.BytesIO(contents))
                stats = get_stats(markdown, page_count)
                md_name = f.filename.rsplit(".", 1)[0] + ".md"
                zf.writestr(md_name, markdown)
                results.append({
                    "file": f.filename,
                    "output": md_name,
                    "stats": stats,
                    "error": None
                })
            except Exception as e:
                results.append({"file": f.filename, "error": str(e)})

    zip_buffer.seek(0)

    # Return zip as streaming download + results summary in header
    import json, base64
    summary = base64.b64encode(json.dumps(results).encode()).decode()

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=token-messiah-batch.zip",
            "X-Batch-Results": summary,
        }
    )


# ─── Groq AI Polish ───────────────────────────────────────────────────────────

@app.post("/polish")
async def polish(request: Request):
    body        = await request.json()
    markdown    = body.get("markdown", "").strip()
    groq_key    = os.environ.get("GROQ_API_KEY", "")

    if not markdown:
        raise HTTPException(400, "No markdown provided.")
    if not groq_key:
        raise HTTPException(503, "AI polish is not configured on the server.")

    # Truncate to keep within Groq's context limit
    chunk = markdown[:8000] + ("\n\n[...document truncated for AI polish...]" if len(markdown) > 8000 else "")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "temperature": 0.1,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a markdown cleanup assistant. Your only job is to fix extracted PDF text. "
                                "Fix merged words (SmartGrids → Smart Grids), fix missing spaces after punctuation, "
                                "remove duplicate lines, remove page numbers and journal footers, "
                                "fix broken sentences from column layout mixing, remove (cid:NNN) artifacts. "
                                "Preserve ALL real content — do not summarize, shorten, or add anything new. "
                                "Return ONLY the cleaned markdown with no preamble, explanation, or code fences."
                            )
                        },
                        {
                            "role": "user",
                            "content": f"Clean up this extracted PDF markdown:\n\n{chunk}"
                        }
                    ]
                }
            )
        print(f"Groq status: {res.status_code}")
        print(f"Groq response: {res.text[:500]}")

        data = res.json()

        # Groq returned an API error (wrong key, rate limit, etc.)
        if "error" in data:
            err_msg = data["error"].get("message", "Unknown Groq error")
            raise HTTPException(502, f"Groq API error: {err_msg}")

        # Unexpected response shape
        if "choices" not in data or not data["choices"]:
            raise HTTPException(502, f"Unexpected Groq response: {str(data)[:200]}")

        cleaned = data["choices"][0]["message"]["content"].strip()
        return JSONResponse({"markdown": cleaned})
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "AI polish timed out. Try again.")
    except Exception as e:
        print(f"Polish exception: {str(e)}")
        raise HTTPException(500, f"AI polish failed: {str(e)}")