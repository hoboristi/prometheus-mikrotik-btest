FROM python:3.12-slim

WORKDIR /app
COPY main.py .
ENV API_USER=admin \
    API_PASS=secret_password \
    API_PORT=8728 \
    EXPORTER_PORT=9115 \
    TEST_DURATION=10
EXPOSE 9115
ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
