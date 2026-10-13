FROM python:3.12.14-slim
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src /app/src
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8765
ENTRYPOINT ["python", "-m", "seiche_agent_network.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765", "--gateway"]
