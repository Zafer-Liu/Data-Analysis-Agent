FROM python:3.11-slim

# System dependencies needed by Python packages:
# - unixodbc + libodbc2: pyodbc (SQL Server)
# - libgomp1: numpy / scikit-learn / onnxruntime (OpenMP)
# - libgl1 + libglib2.0-0: matplotlib chart rendering
# - curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc \
    libodbc2 \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (static/dist/ is committed, so bundles are included)
COPY . .

# Sample data for cloud demo (Railway only, not used in local dev)
COPY deploy/samples/ /app/deploy_samples/

# Railway injects PORT env var; app.py already reads it
ENV BAA_HOST=0.0.0.0
ENV BAA_SKIP_DEPENDENCY_CHECK=1
ENV PYTHONUNBUFFERED=1

EXPOSE 5001

CMD ["python", "-u", "app.py"]
