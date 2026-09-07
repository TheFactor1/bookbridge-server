# The setup wizard: docker CLI + compose (to drive the stack) and python3 (to
# serve the wizard). Alpine-small; no pip dependencies -- server.py is stdlib.
FROM docker:27-cli
RUN apk add --no-cache python3 docker-cli-compose
WORKDIR /app
COPY server.py ./
COPY ui ./ui
EXPOSE 8090
CMD ["python3", "/app/server.py"]
