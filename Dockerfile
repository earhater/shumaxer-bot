FROM python:3.12-trixie

WORKDIR /app

ADD main.py .
RUN pip3 install aiogram sqlite3 python-dotenv

RUN mkdir -p /app/data

CMD ["python", "main.py"]

