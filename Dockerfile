FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Room / state dirs (ephemeral unless you mount a volume)
RUN mkdir -p data/rooms

EXPOSE 5000
CMD gunicorn -b 0.0.0.0:${PORT:-5000} --workers 1 --threads 8 --timeout 120 app:app
