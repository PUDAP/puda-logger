FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 logger \
    && useradd --uid 10001 --gid logger --no-create-home logger
COPY storage.py subscriber.py ./
USER 10001:10001
ENTRYPOINT ["python", "subscriber.py"]
