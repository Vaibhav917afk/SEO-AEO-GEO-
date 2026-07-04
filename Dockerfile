FROM python:3.12-slim

WORKDIR /app
COPY . .

ENV AWA_HOST=0.0.0.0
ENV AWA_PORT=8787

EXPOSE 8787
CMD ["python", "-m", "backend.app"]
