FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/app/input_files", "/app/output"]

ENTRYPOINT ["python", "pickles_transducer.py"]
CMD ["sts"]
