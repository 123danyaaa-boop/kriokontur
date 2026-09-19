FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install -r requirements.txt --no-cache-dir

CMD ["python", "run.py", "--skip-tests", "--no-browser"]