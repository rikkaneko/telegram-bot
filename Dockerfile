FROM python:3.10.8-slim

VOLUME /app/data

COPY requirements.txt /tmp/
RUN --mount=type=cache,target=/root/.cache pip install --prefer-binary -r /tmp/requirements.txt

# Create non-root user
RUN useradd -u 1000 user

COPY --chmod=644 main.py .env /app/
WORKDIR /app

USER user

CMD [ "python", "main.py" ]