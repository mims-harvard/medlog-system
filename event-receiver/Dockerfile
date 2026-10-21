FROM python:3.11-slim

WORKDIR /app

# Keep the same sibling directories as the source checkout (see schema_paths.py).
COPY schemas/ /app/schemas/
COPY event-receiver/ /app/event-receiver/

WORKDIR /app/event-receiver
RUN pip install --no-cache-dir .

EXPOSE 5000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
