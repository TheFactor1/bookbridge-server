FROM python:3.13-alpine
WORKDIR /app
COPY main.py .
EXPOSE 8089
# Stdlib only -- no pip install, nothing to audit or keep patched beyond the
# base image itself, same as the other shelfmark relays.
CMD ["python3", "main.py"]
