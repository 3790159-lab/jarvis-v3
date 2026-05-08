FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn requests pydantic

COPY jarvis_n8n_bridge.py /app/jarvis_n8n_bridge.py

CMD ["uvicorn", "jarvis_n8n_bridge:app", "--host", "0.0.0.0", "--port", "8030"]