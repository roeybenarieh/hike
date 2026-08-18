{ pkgs, ... }: {

  # ── GUI ───────────────────────────────────────────────────────────────────
  # DbGate: single MIT-licensed GUI for PostgreSQL, MongoDB, and Redis.
  # Connections pre-seeded into a project-local workspace on first run.
  # Opens as a desktop (Electron) window when launched with `devenv up dbgate`.
  packages = [ pkgs.dbgate ];

  processes.dbgate.exec = ''
    WS="$DEVENV_ROOT/.devenv/dbgate-workspace"
    mkdir -p "$WS"
    if [ ! -f "$WS/connections.jsonl" ]; then
      cat > "$WS/connections.jsonl" <<EOF
{"_id":"hike-postgres","engine":"postgres@dbgate-plugin-postgres","server":"127.0.0.1","port":5432,"user":"$USER","database":"hike","displayName":"hike (PostgreSQL)","unsaved":false}
{"_id":"hike-mongo","engine":"mongo@dbgate-plugin-mongo","server":"127.0.0.1","port":27017,"user":"admin","password":"secret","displayName":"hike (MongoDB)","unsaved":false}
{"_id":"hike-redis","engine":"redis@dbgate-plugin-redis","server":"127.0.0.1","port":6379,"displayName":"hike (Redis)","unsaved":false}
EOF
    fi
    WORKSPACE_DIR="$WS" dbgate
  '';

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
