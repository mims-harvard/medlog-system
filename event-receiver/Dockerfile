FROM python:3.11-slim

WORKDIR /app

# openapi.yaml must sit one level above event-receiver/ to match app.py's parent.parent lookup.
COPY openapi.yaml /app/openapi.yaml
COPY event-receiver/ /app/event-receiver/

WORKDIR /app/event-receiver
RUN pip install --no-cache-dir .

EXPOSE 5000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
