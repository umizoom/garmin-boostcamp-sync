FROM python:3.12-slim

# git is needed to install boostcampapi from GitHub
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && apt-get purge -y git && apt-get autoremove -y

COPY exercise_map.toml ./

ENV PYTHONUNBUFFERED=1 \
    GBSYNC_CONFIG=/app/credentials.toml \
    GBSYNC_DATA_DIR=/app/data \
    GBSYNC_EXERCISE_MAP=/app/exercise_map.toml

USER app
ENTRYPOINT ["gbsync"]
CMD ["daemon"]
