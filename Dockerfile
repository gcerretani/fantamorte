FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    gcc \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh && \
    SECRET_KEY=build-time-placeholder \
    DATABASE_URL=sqlite:////tmp/build.db \
    python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "fantamorte_project.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60"]
