FROM python:3.13-slim
RUN pip install --no-cache-dir boto3
COPY runner.py /opt/ddp/runner.py
ENTRYPOINT ["python", "/opt/ddp/runner.py"]
