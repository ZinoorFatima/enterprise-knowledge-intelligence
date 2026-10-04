FROM python:3.11-slim

# PyMuPDF needs no system libs on slim; tesseract is optional (OCR_ENGINE=tesseract).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

COPY api /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
