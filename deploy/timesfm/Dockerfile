FROM python:3.12-slim
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
WORKDIR /app
RUN pip install --no-cache-dir torch==2.9.1 --index-url ${TORCH_INDEX_URL} \
    && pip install --no-cache-dir "timesfm==3.0.2"
COPY service.py /app/service.py
RUN useradd --create-home --uid 10001 forecast
USER forecast
EXPOSE 8787
ENV TIMESFM_HOST=0.0.0.0 TIMESFM_PORT=8787 TIMESFM_MODEL_DIR=/model TIMESFM_DEVICE=cpu
CMD ["python", "/app/service.py"]
