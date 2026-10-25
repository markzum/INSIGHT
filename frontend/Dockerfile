FROM node:22.16.0-alpine
WORKDIR /app
COPY . .
RUN node scripts/build.cjs && node --test tests/*.test.cjs
ENV PORT=4173
ENV HOST=0.0.0.0
EXPOSE 4173
CMD ["node", "scripts/serve.cjs", "--dist"]
