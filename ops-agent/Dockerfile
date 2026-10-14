FROM python:3.12-slim

# docker-cli so that code execution inside this container can talk to the
# mounted /var/run/docker.sock (the intentional privesc pivot for this room).
RUN apt-get update && \
    apt-get install -y --no-install-recommends docker-cli ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8083
ENTRYPOINT ["./entrypoint.sh"]
