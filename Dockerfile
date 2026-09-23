FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install -r requirements.txt --no-cache-dir

# Контейнер должен быть доступен снаружи, поэтому слушаем все интерфейсы явно.
# Локальный python run.py по умолчанию слушает только 127.0.0.1.
ENV KRIOKONTUR_HOST=0.0.0.0

CMD ["python", "run.py", "--skip-tests", "--no-browser"]