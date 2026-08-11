# ---------------------------------------------------------------------------
# Build em dois estágios: o primeiro compila as wheels, o segundo só instala.
# O resultado é uma imagem sem compilador, sem cache do pip e sem código-fonte
# de build — menor e com menos superfície de ataque.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip wheel

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip wheel --no-cache-dir --wheel-dir /wheels .


# ---------------------------------------------------------------------------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Fluxor" \
      org.opencontainers.image.description="Motor de automações declarativas em YAML" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Rodar como root em container que executa `shell.run` seria pedir problema.
RUN useradd --create-home --uid 1000 fluxor

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

COPY examples ./examples

RUN mkdir -p /data && chown -R fluxor:fluxor /app /data

USER fluxor

ENV FLUXOR_WORKFLOWS_DIR=/app/examples \
    FLUXOR_DATABASE_URL=sqlite+aiosqlite:////data/fluxor.db \
    FLUXOR_HOST=0.0.0.0 \
    FLUXOR_PORT=8000 \
    FLUXOR_LOG_FORMAT=json \
    FLUXOR_ENABLE_SCHEDULER=true

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["fluxor", "serve", "--host", "0.0.0.0", "--port", "8000"]
