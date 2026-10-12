FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir \
    "fastapi>=0.118.0,<1.0.0" \
    "uvicorn[standard]>=0.37.0,<1.0.0" \
    "openai>=2.0.0,<3.0.0" \
    "pydantic>=2.11.0,<3.0.0" \
    "pydantic-settings>=2.10.0,<3.0.0" \
    "sqlalchemy>=2.0.40,<3.0.0" \
    "psycopg[binary]>=3.2.0,<4.0.0"
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
