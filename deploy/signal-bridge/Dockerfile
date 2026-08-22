FROM python:3.12-alpine
WORKDIR /app
COPY relay.py /app/relay.py
USER 65534:65534
CMD ["python", "/app/relay.py"]
