FROM python:3.12-alpine AS builder

RUN apk update && \
  apk add git

RUN pip install --upgrade pipenv==2025.0.3

# Use pip cache
ENV PIP_CACHE_DIR=/var/cache/pip

WORKDIR /app

COPY Pipfile Pipfile.lock ./

RUN pipenv install --deploy --system 
#--pip-args="--use-feature=fast-deps"

FROM python:3.12-alpine

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

RUN addgroup -S murz_home_bot && adduser -S murz_home_bot -G murz_home_bot --no-create-home

USER murz_home_bot

ARG BOT_TOKEN
ENV murz_home_bot_BOT_TOKEN=${BOT_TOKEN}

ARG DELUGE_ADDR
ENV murz_home_bot_DELUGE_ADDR=${DELUGE_ADDR}

ARG DELUGE_PORT
ENV murz_home_bot_DELUGE_PORT=${DELUGE_PORT}

ARG DELUGE_USERNAME
ENV murz_home_bot_DELUGE_USERNAME=${DELUGE_USERNAME}

ARG DELUGE_PASSWORD
ENV murz_home_bot_DELUGE_PASSWORD=${DELUGE_PASSWORD}

CMD ["python", "main.py"]
