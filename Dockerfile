FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed by endplay (DDS C library)
RUN apt-get update && apt-get install -y \
    libboost-thread-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY index.html .

# Use /tmp for SQLite on cloud (writable)
ENV DATA_DIR=/tmp

EXPOSE 8080

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120"]
