FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ARG PACKAGE
ENV PACKAGE=${PACKAGE} UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY contract ./contract
COPY monarch-proxy ./monarch-proxy
COPY agents/echo ./agents/echo
COPY agents/deep ./agents/deep
COPY gateway ./gateway
COPY prompts ./prompts
RUN uv sync --package ${PACKAGE} --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "exec $PACKAGE"]
