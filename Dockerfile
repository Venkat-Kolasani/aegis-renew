# Expose the service on Render's $PORT (required).
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend

ENV PYTHONPATH=/app
ENV PORT=8000

# Render's Postgres URL is often postgres://; SQLAlchemy + psycopg needs +psycopg.
CMD ["sh", "-c", "export DATABASE_URL=\"$(python -c \"import os; u=os.environ.get('DATABASE_URL',''); print(u.replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1) if u else u\")\"; uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
