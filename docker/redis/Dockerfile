FROM redis:alpine

ENV REDIS_LOG_LEVEL=notice

CMD ["sh", "-c", "redis-server --save 60 1 --loglevel ${REDIS_LOG_LEVEL} --requirepass ${REDIS_PASSWORD}"]
