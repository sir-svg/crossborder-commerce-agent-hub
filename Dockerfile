FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
COPY app ./app
RUN uv sync --frozen --no-dev --extra milvus
RUN useradd --create-home commerce && mkdir -p /app/data && chown -R commerce:commerce /app
USER commerce
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
