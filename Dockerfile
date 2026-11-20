FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS web
WORKDIR /web
RUN npm install --global --ignore-scripts pnpm@10.28.1
COPY web/package.json web/pnpm-lock.yaml web/.npmrc ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY web/ ./
RUN pnpm check && pnpm build

FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app

FROM python-base AS deps
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-build --no-cache

FROM deps AS dev
RUN uv sync --locked --no-install-project --no-build --no-cache
COPY src/ ./src/
COPY tests/ ./tests/
COPY --from=web /web/build ./web/build/
ENV PATH=/app/.venv/bin:$PATH

FROM python-base AS runtime
LABEL io.modelcontextprotocol.server.name="io.github.psyb0t/modembox"
RUN groupadd --gid 10001 modembox && useradd --uid 10001 --gid modembox --no-create-home modembox && install -d -m 0700 -o modembox -g modembox /data
COPY --from=deps /app/.venv /app/.venv
COPY --from=web /web/build /app/ui
COPY src/ ./src/
ENV PATH=/app/.venv/bin:$PATH MODEMBOX_DATA_DIR=/data MODEMBOX_WEB_DIR=/app/ui
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).close()"]
ENTRYPOINT ["python", "-m", "modembox"]
