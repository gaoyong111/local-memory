FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/

ENV MEMORY_DIR=/data \
    MEMORY_MCP_TRANSPORT=http \
    MEMORY_HTTP_HOST=0.0.0.0 \
    MEMORY_HTTP_PORT=8000

VOLUME /data
EXPOSE 8000

CMD ["python3", "/app/src/mcp_server.py"]
