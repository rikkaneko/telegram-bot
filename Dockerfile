FROM python:3.10.8

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

VOLUME /app

CMD [ "python", "./main.py" ]