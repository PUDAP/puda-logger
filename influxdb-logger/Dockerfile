FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml main.py ./
RUN pip install --no-cache-dir .

CMD ["python", "-u", "main.py"]
