FROM python:3.12-alpine

WORKDIR /app
COPY server.py checklist_items.py index.html ./
COPY favicon.svg favicon-32.png apple-touch-icon.png ./

ENV PORT=8080
ENV DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8080

# No pip install — everything used is Python standard library.
CMD ["python", "server.py"]
