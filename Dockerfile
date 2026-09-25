# Python stdlib server: static site + /api/leads/<slug> relay to GHL inbound webhooks.
# Set GHL_WEBHOOK_URL_QUOTE and GHL_WEBHOOK_URL_CONTACT in Railway variables.
FROM python:3.12-alpine
WORKDIR /srv
ENV PYTHONUNBUFFERED=1
COPY . ./
EXPOSE 8080
CMD ["python", "server.py"]
