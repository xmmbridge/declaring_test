FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed by endplay (DDS C library)
RUN apt-get update && apt-get install -y \
    libboost-thread-dev \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download BEN ONNX defender models (~2.65 MB each, ~10 MB total).
# These power the bridge defensive tiebreaker. Build continues if download
# fails — app falls back to heuristics gracefully.
RUN mkdir -p /app/ben_models \
    && for model in lefty_nt righty_nt lefty_suit righty_suit; do \
         curl --fail -sL \
           "https://raw.githubusercontent.com/lorserker/ben/master/models/onnx/${model}.onnx" \
           -o "/app/ben_models/${model}.onnx" \
         || echo "BEN: could not download ${model}.onnx — heuristics will be used"; \
       done

# Copy application files
COPY app.py .
COPY index.html .
COPY gunicorn.conf.py .

# Use /tmp for SQLite on cloud (writable)
ENV DATA_DIR=/tmp

EXPOSE 8080

CMD ["gunicorn", "app:app", "-c", "gunicorn.conf.py"]
