# Build context: the repo root (so the image can carry prompts/).
FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production SYSTEM_PROMPT_PATH=/app/prompts/system.md PI_DATA_DIR=/app/data/agents/pi
COPY agents/pi/package.json agents/pi/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY agents/pi/src ./src
# The shared system prompt; a run-time mount over /app/prompts overrides it (see docker-compose.yml).
COPY prompts ./prompts
USER node
CMD ["node", "src/server.ts"]
