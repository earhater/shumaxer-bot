FROM python:3.12-trixie
ADD main.py .
RUN pip3 install aiogram python-dotenv
CMD ["python", "main.py"]

