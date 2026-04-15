FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/output /app/storage/pdfs /app/storage/ocr_output

EXPOSE 8080

CMD ["gunicorn", "server:app", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120"]
