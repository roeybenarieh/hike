{ pkgs, ... }: {

  # ── PostgreSQL 16 ──────────────────────────────────────────────────────────
  # Matches testcontainers image: postgres:16
  # sqlalchemy provider uses psycopg driver (psycopg[binary] extra).
  # Connection: postgresql+psycopg://localhost:5432/hike
  services.postgres = {
    enable           = true;
    package          = pkgs.postgresql_16;
    port             = 5432;
    listen_addresses = "127.0.0.1";
    initialDatabases = [{ name = "hike"; }];
  };

  # ── Redis 7 ───────────────────────────────────────────────────────────────
  # Matches testcontainers image: redis:7
  # RedisDBContext uses MULTI/EXEC pipeline transactions (atomic).
  # Connection: redis://127.0.0.1:6379
  services.redis = {
    enable  = true;
    port    = 6379;
    package = pkgs.redis;
  };

  # ── MongoDB – single-node replica set (rs0) ────────────────────────────────
  # PyMongoDBContext requires a replica set; standalone mongod does NOT
  # support multi-document ACID transactions.
  # Connection: mongodb://127.0.0.1:27017/?directConnection=true
  services.mongodb = {
    enable                  = true;
    replication.enable      = true;
    replication.replSet     = "rs0";
    additionalArgs          = [ "--port" "27017" "--bind_ip" "127.0.0.1" ];
    initDatabaseUsername    = "admin";
    initDatabasePassword    = "secret";
  };
}
