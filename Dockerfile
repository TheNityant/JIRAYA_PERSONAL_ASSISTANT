FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
# Install the complete application, including OAuth, encryption and timezone data.
# Keep pyproject.toml as the single dependency list for local and cloud installs.
RUN python -m pip install --no-cache-dir . && python -m pip check
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
