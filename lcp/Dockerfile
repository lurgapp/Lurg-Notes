FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 lcp
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
USER lcp
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
