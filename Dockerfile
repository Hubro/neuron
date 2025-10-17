FROM python:3.13-alpine

RUN apk add build-base   # For building Python dependencies

RUN pip --isolated --no-cache-dir install poetry && \
    mkdir /app

# Seems like Home Assistant Addons need to run as root, otherwise they can't
# read their own options. Doesn't seem to be a way around it.
#
# RUN adduser -H -S -D neuron && \
#     id neuron && \
#     chown neuron /app
# 
# USER neuron

WORKDIR /app

ENV POETRY_CONFIG_DIR=/app/.poetry/config
ENV POETRY_DATA_DIR=/app/.poetry/data
ENV POETRY_CACHE_DIR=/app/.poetry/cache
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --without=dev

COPY . ./
RUN poetry install --only-root

# Poetry overrides PYTHONPATH, so this doesn't work :(
# ENV PYTHONPATH="/config"

ENV PYTHONUNBUFFERED=1

ENTRYPOINT [ "poetry", "run", "python", "-m", "neuron" ]
