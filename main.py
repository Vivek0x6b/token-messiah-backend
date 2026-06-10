from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from converter import extract_markdown, get_stats
import io

app = FastAPI(title="PDF to Markdown API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://token-messiah-frontend.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


@app.get("/")
def root():
    return {"status": "ok", "message": "PDF to Markdown API is running"}


@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    contents = await file.read()

    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max size is 50MB.")

    try:
        file_obj = io.BytesIO(contents)
        markdown, page_count = extract_markdown(file_obj)
        stats = get_stats(markdown, page_count)
        return JSONResponse({
            "markdown": markdown,
            "stats": stats
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")
