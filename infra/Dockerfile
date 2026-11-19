FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn==0.30.6 pydantic==2.9.2

COPY cloud_worker.py .

EXPOSE 8000

CMD ["uvicorn", "cloud_worker:app", "--host", "0.0.0.0", "--port", "8000"]