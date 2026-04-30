FROM python:3.12-slim
WORKDIR /app
RUN addgroup --system app && adduser --system --group app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8002/health')" || exit 1
USER app
EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
