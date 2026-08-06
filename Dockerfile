FROM python:3.11-slim

# System dependencies:
# - unixodbc + libodbc2: pyodbc (SQL Server) runtime
# - curl: healthcheck probe
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc \
    libodbc2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (static/dist/ is committed, so bundles are included)
COPY . .

# Railway injects PORT env var; app.py already reads it
ENV BAA_HOST=0.0.0.0
ENV BAA_SKIP_DEPENDENCY_CHECK=1

EXPOSE 5001

CMD ["python", "app.py"]
