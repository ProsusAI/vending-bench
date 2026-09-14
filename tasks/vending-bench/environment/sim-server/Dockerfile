FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vending/ ./vending/
COPY server.py .
COPY variants/ /config/
COPY vending/config.toml /vending/config.toml

RUN mkdir -p /state
ENV VENDING_STATE_PATH=/state/state.json \
    VENDING_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "server.py"]
