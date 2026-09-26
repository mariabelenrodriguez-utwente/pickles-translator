FROM haskell:9.8.4 AS builder

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app
COPY . .
RUN stack setup && stack build --copy-bins

FROM debian:bookworm-slim

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends netbase z3 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /app/specs
COPY --from=builder /root/.local/bin/lattest-tester-exe /usr/local/bin/lattest-tester-exe

ENTRYPOINT ["lattest-tester-exe"]