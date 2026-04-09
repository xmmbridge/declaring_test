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
COPY gunicorn.conf.py .

# Use /tmp for SQLite on cloud (writable)
ENV DATA_DIR=/tmp

EXPOSE 8080

CMD ["gunicorn", "app:app", "-c", "gunicorn.conf.py"]
