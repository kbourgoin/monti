FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY src ./src
# Sessions and the prompt are mounted at run time (see docker-compose.yml).
USER node
CMD ["node", "src/server.ts"]
